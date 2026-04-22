import os
from datetime import datetime
import json


class MemoryManager:
    def __init__(self, memory_dir="memory"):
        self.memory_dir = memory_dir
        self._ensure_memory_dir()

    def _ensure_memory_dir(self):
        """确保记忆目录存在"""
        if not os.path.exists(self.memory_dir):
            os.makedirs(self.memory_dir)

    def _get_memory_file_path(self, agent_name):
        """获取记忆文件路径"""
        return os.path.join(self.memory_dir, f"{agent_name}_memory.md")

    def save_memory(self, agent_name, memory_data):
        """保存记忆到 MD 文件"""
        file_path = self._get_memory_file_path(agent_name)
        
        content = f"# {agent_name} - 训练记忆\n\n"
        content += f"创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        content += "---\n\n"
        
        for i, item in enumerate(memory_data, 1):
            content += f"## 学习记录 {i}\n\n"
            content += f"**考点**: {item.get('topic', '')}\n\n"
            content += f"**时间**: {item.get('timestamp', '')}\n\n"
            
            if item.get('search_results'):
                content += "### 搜索结果\n\n"
                for j, result in enumerate(item['search_results'], 1):
                    content += f"{j}. {result.get('title', '')}\n"
                    content += f"   {result.get('snippet', '')}\n\n"
            
            content += "### 学习总结\n\n"
            content += f"{item.get('knowledge', '')}\n\n"
            
            if item.get('teacher_feedback'):
                content += "### 老师反馈\n\n"
                content += f"{item.get('teacher_feedback', '')}\n\n"
            
            content += "---\n\n"
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"💾 记忆已保存到: {file_path}")

    def load_memory(self, agent_name):
        """从 MD 文件加载记忆"""
        file_path = self._get_memory_file_path(agent_name)
        
        if not os.path.exists(file_path):
            print(f"📭 未找到记忆文件: {file_path}")
            return []
        
        print(f"📂 正在加载记忆: {file_path}")
        
        # 简单的 MD 解析，提取关键信息
        memory_data = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 这里做简单解析，实际使用中可以根据需要完善
            # 由于是我们自己保存的格式，我们可以用更结构化的方式保存
            # 让我们同时保存一个 JSON 格式的备份
            json_path = file_path.replace('.md', '.json')
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    memory_data = json.load(f)
                print(f"✅ 成功加载 {len(memory_data)} 条记忆")
                return memory_data
            else:
                print("⚠️  未找到 JSON 格式记忆文件，尝试从 MD 解析")
                return []
        except Exception as e:
            print(f"❌ 加载记忆失败: {e}")
            return []

    def save_structured_memory(self, agent_name, memory_data):
        """同时保存 JSON 格式的结构化记忆"""
        # 保存 MD 格式
        self.save_memory(agent_name, memory_data)
        
        # 保存 JSON 格式
        json_path = os.path.join(self.memory_dir, f"{agent_name}_memory.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
