import requests
from bs4 import BeautifulSoup
import time
import random


class SearchTool:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def search_duckduckgo(self, query, num_results=5):
        """使用 DuckDuckGo 搜索"""
        try:
            url = f"https://html.duckduckgo.com/html/"
            params = {'q': query}
            
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            for result in soup.find_all('div', class_='result')[:num_results]:
                title_elem = result.find('a', class_='result__a')
                snippet_elem = result.find('a', class_='result__snippet')
                
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    link = title_elem.get('href', '')
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''
                    
                    results.append({
                        'title': title,
                        'link': link,
                        'snippet': snippet
                    })
            
            return results
        except Exception as e:
            print(f"搜索出错: {e}")
            return []

    def search(self, query, num_results=5):
        """搜索主方法"""
        print(f"🔍 正在搜索: {query}")
        results = self.search_duckduckgo(query, num_results)
        
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
