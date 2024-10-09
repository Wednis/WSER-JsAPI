'''
WSER用于前端js文件分析的js路径获取器，仅获取同域名的js路径
基于已有的响应 --> js路径获取
'''

import re
import requests
from requests.exceptions import SSLError
from concurrent.futures import ThreadPoolExecutor
import time


class JsFinder:
    def __init__(self, responselist:list, domain:str, funcs:list=[]) -> None:
        """基于已有响应的文本匹配实现递归获取js文件的url\n
        loadfuncs参数用于在获取js文件的历史响应文本中自定义方法对文本进行匹配，需返回获取到的js路径，以迭代器的形式"""
        if domain.startswith('http'):
            domain = domain.split('/')[2]
        
        self.domain = domain
        self.pattern = re.compile(r'[cf][ ]?=[ ]?[\'"]?([a-zA-Z0-9_/\\\-\.]{1,}\.js)[\'">]?|[\'"]([a-zA-Z0-9_/\\\-\.]{1,}\.js)[\'"]')
        self._session = requests.session()
        self._tmpjspaths = set()              # 临时（未经验证是否404）
        self.result = set()
        self.responselist = responselist     # 储存的响应

        # 先匹配传入的响应
        for text in [res.text for res in responselist]:
            match_result = self.pattern.findall(text)
            match_result = [j for i in match_result for j in i if j != '']      # 每部分都是两个元素的元组

            for jspath in match_result:
                if 'http://' in jspath or 'https://' in jspath:
                    # 不是同域名则跳过
                    if jspath.split('/')[2] != domain:
                        continue
                # 如果是//xxx这种那么就不是同host的js文件
                elif jspath.startswith('//'):
                    continue

                self._tmpjspaths.add(jspath)

        with ThreadPoolExecutor(max_workers=5) as pool:
            f_pool = []
            # 递归获取
            for jspath in self._tmpjspaths.copy():
                f = pool.submit(self._run, jspath)
                f_pool.append(f)

            # 等待线程完成
            for f in f_pool:
                while not f.done():
                    time.sleep(0.01)

            # 在获取到可获取的全部响应后进行自定义的文本匹配（还需要再次进行_run筛选）
            if funcs:
                f_pool = []

                for f in funcs:
                    f = pool.submit(self._func, f)
                    f_pool.append(f)

                # 等待线程完成
                for f in f_pool:
                    while not f.done():
                        time.sleep(0.01)

                f_pool = []

                # 再次进行筛选
                for jspath in self._tmpjspaths.copy():
                    f = pool.submit(self._run, jspath)
                    f_pool.append(f)

                # 等待线程完成
                for f in f_pool:
                    while not f.done():
                        time.sleep(0.01)


    def _run(self, jspath:str):
        """从响应中获取到的js的url的响应中进一步获取js的url"""
        try:
            jsurl = self._join(('https://' + self.domain + '/'), jspath)
            res = self._session.get(url=jsurl,
                                    headers={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.6; rv:2.0.1) Gecko/20100101 Firefox/4.0.1'},
                                    timeout=3)
        except SSLError:
            jsurl = self._join(('http://' + self.domain + '/'), jspath)
            res = self._session.get(url=jsurl,
                                    headers={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.6; rv:2.0.1) Gecko/20100101 Firefox/4.0.1'},
                                    timeout=3)
        except:
            return
        # 404
        if res.status_code == 404:
            return

        # 添加到最终结果（http://xxx.xxx/static/js/xxx这种形式）（能执行到这一步说明是非404的）
        self.responselist.append(res)
        self.result.add(jsurl)

        # 匹配
        res.encoding = res.apparent_encoding
        match_result = self.pattern.findall(res.text)
        match_result = [j for i in match_result for j in i if j != '']

        for jspath in match_result:
            if 'http://' in jspath or 'https://' in jspath:
                # 不是同域名则跳过
                if jspath.split('/')[2] != self.domain:
                    continue
            # 如果是//xxx这种那么就不是同host的js文件
            elif jspath.startswith('//'):
                continue

            jspath = '/'.join(self._join(jsurl, jspath).split('/')[3:])

            # 如果不存在待验证的js路径时则递归获取一下
            if jspath not in self._tmpjspaths:
                self._run(jspath)
    
    def _func(self, f):
        """使得可以用于线程"""
        result = f(self.responselist)
        for i in result:
            self._tmpjspaths.add(i)


    @staticmethod
    def _join(url:str, jspath:str):
        """连接url和jspath"""
        # 使用栈来连接
        urlparts_stack = []

        urllist = [i for i in url.split('/') if i != '']
        urllist[0] = urllist[0] + '//' + urllist[1]    # 连接请求类型和host
        del urllist[1]

        # 去除末尾的例如xx.xxx的
        if len(urllist) != 1 and '.' in urllist[-1]:
            urllist.pop()

        pathlist = [i for i in jspath.split('/') if i != '']

        # 以/aa开头那么就说明是直接从根目录开始
        if jspath.startswith('/'):
            urlparts_stack = [urllist[0]]
    
        else:
            urlparts_stack = urllist

        for i in pathlist:
            if i == '.':
                continue
            elif i != '..':
                urlparts_stack.append(i)
            elif i == '..' and len(urlparts_stack) != 1:
                urlparts_stack.pop()     # 如果为..就说明需要往上

        return '/'.join(urlparts_stack)



def func_1(responselist:list):
    """用于匹配chunk这种分隔开的表示方法"""
    result = []
    for res in responselist:
        res.encoding = res.apparent_encoding
        basepath = re.findall(r'return.{1,10}"(.*?js[/]?)"[ ]?\+', res.text)
        if basepath:
            # 如果不为[]
            basepath = basepath[0]
            chunklist = re.search(r'return.{1,10}".*?js[/]?".*?({".*?"})', res.text, flags=re.DOTALL)[0].split(',')
            chunkdict = {}
            for i in chunklist:
                try:
                    # 赋值
                    exec(f"chunkdict[{i.split(':')[0]}]={i.split(':')[1]}", locals())
                except:
                    pass
            for i in chunkdict:
                result.append(basepath + i + '.' + chunkdict[i] + '.js')

    return result



if __name__ == '__main__':
    res = requests.get('http://example.com/')
    a = JsFinder(responselist=[res], domain='example.com', funcs=[func_1])
    for i in a.result:
        print(i)
