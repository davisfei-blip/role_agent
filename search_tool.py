import requests
from bs4 import BeautifulSoup
import time
import random
import urllib.parse


class SearchTool:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        }

    def search_baidu(self, query, num_results=5):
        """使用百度搜索"""
        try:
            # 先访问百度首页获取cookie
            session = requests.Session()
            session.get('https://www.baidu.com', headers=self.headers, timeout=10)

            # 百度搜索URL
            url = "https://www.baidu.com/s"
            params = {
                'wd': query,
                'rn': num_results * 3,
                'ie': 'utf-8',
                'tn': 'baidu',
            }

            response = session.get(
                url,
                params=params,
                headers=self.headers,
                timeout=15,
                allow_redirects=True
            )
            response.raise_for_status()
            response.encoding = 'utf-8'

            soup = BeautifulSoup(response.text, 'html.parser')
            results = []

            # 百度搜索结果容器 - 新版百度
            containers = soup.find_all('div', class_='result') or soup.find_all('div', class_='c-container')

            # 如果找不到，尝试其他选择器
            if not containers:
                # 尝试找所有包含标题和摘要的结构
                all_divs = soup.find_all('div')
                for div in all_divs:
                    h3 = div.find('h3')
                    if h3 and h3.find('a'):
                        containers.append(div)

            for container in containers[:num_results * 2]:
                # 标题
                title_elem = container.find('h3')
                if not title_elem:
                    continue

                link_elem = title_elem.find('a')
                if not link_elem:
                    continue

                title = link_elem.get_text(strip=True)
                link = link_elem.get('href', '')

                # 过滤广告
                if not title or '广告' in title or '百度推广' in title or len(title) < 5:
                    continue

                # 摘要 - 尝试多种选择器
                snippet = ''
                snippet_selectors = [
                    'span.content-right_8Zs40',
                    'div.content-right_8Zs40',
                    'span.c-color-text',
                    'div.c-abstract',
                    'div.content-right',
                    'span.g',
                    'div.c-span9',
                ]

                for selector in snippet_selectors:
                    parts = selector.split('.')
                    tag = parts[0]
                    cls = parts[1] if len(parts) > 1 else None
                    if cls:
                        elem = container.find(tag, class_=cls)
                    else:
                        elem = container.find(tag)
                    if elem:
                        snippet = elem.get_text(strip=True)
                        if len(snippet) > 10:
                            break

                # 如果还找不到，尝试通用方式
                if not snippet or len(snippet) < 10:
                    for elem in container.find_all(['div', 'span', 'p']):
                        text = elem.get_text(strip=True)
                        if text != title and 20 < len(text) < 500:
                            snippet = text
                            break

                if not snippet or len(snippet) < 10:
                    continue

                results.append({
                    'title': title,
                    'link': link,
                    'snippet': snippet
                })

                if len(results) >= num_results:
                    break

            return results

        except requests.exceptions.Timeout:
            print(f"   百度搜索超时，请检查网络连接")
            return []
        except requests.exceptions.ConnectionError:
            print(f"   无法连接到百度，请检查网络")
            return []
        except Exception as e:
            print(f"   百度搜索出错: {e}")
            return []

    def search(self, query, num_results=5):
        """搜索主方法"""
        print(f"🔍 正在搜索: {query}")
        results = self.search_baidu(query, num_results)

        if results:
            print(f"   找到 {len(results)} 条结果")
        else:
            print("   未找到搜索结果")

        return results

    def format_search_results(self, results):
        """格式化搜索结果为文本"""
        if not results:
            return "未找到相关搜索结果"

        formatted = "搜索结果:\n"
        for i, result in enumerate(results, 1):
            formatted += f"\n{i}. {result['title']}\n"
            formatted += f"   {result['snippet']}\n"
            formatted += f"   链接: {result['link']}\n"

        return formatted
