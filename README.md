# Agent训练系统

一个基于大模型的老师-学生Agent训练系统，学生Agent完全可配置！通过配置文件就可以添加任意数量的学生，每个学生都有自己的prompt、考点配置。

## 项目结构

```
agent_422/
├── main.py                 # 主程序入口
├── teacher_agent.py        # 老师Agent（出考点、点评、反馈）
├── student_agent.py        # 学生Agent基类和创建函数
├── search_tool.py         # 搜索工具模块
├── memory.py              # 记忆管理模块
├── config.yaml             # 考点配置文件（可自定义）
├── config.py             # 配置管理
├── requirements.txt        # 依赖包
├── .env.example           # 环境变量示例
├── prompts/               # 存放学生Agent优化后的prompt
│   ├── strategy_product_prompt.txt
│   └── content_operation_prompt.txt
├── memory/               # 存放学生Agent的记忆文件（自动生成）
└── README.md
```

## 安装步骤

1. 安装依赖包：
```bash
pip install -r requirements.txt
```

2. 配置环境变量：
```bash
cp .env.example .env
```

然后编辑 `.env` 文件，填入你的API Key：
```
OPENAI_API_KEY=your_actual_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

## 使用方法

### 1. 配置学生Agent

编辑 `config.yaml` 文件，在 `students` 部分添加或修改学生配置：

```yaml
students:
  - name: "学生名称"
    role: "角色描述"
    prompt_file: "prompts/学生1_prompt.txt"
    default_prompt: |
      默认的prompt内容...
    config_key: "student1"
```

### 2. 配置学生考点

在 `config.yaml` 中为每个学生配置考点（与 `config_key` 对应）：

```yaml
student1:
  role: "角色描述"
  max_iterations: 3
  topics:
    - name: "考点1"
      description: "考点描述"
      questions:
        - "问题1"
        - "问题2"
```

### 3. 运行训练

```bash
python main.py
```

程序会自动列出所有配置的学生，选择要训练的即可。

## 训练流程

1. **加载记忆**：学生Agent自动加载之前的训练记忆（如果有）
2. **学生搜索学习**：如果启用搜索，学生Agent先搜索全网相关信息
3. **学生学习**：结合搜索结果和历史记忆学习考点知识
4. **考试答题**：学生Agent回答考点问题
5. **老师点评**：老师Agent评估答案并打分
6. **反馈迭代**：学生Agent根据老师反馈优化自己的prompt
7. **保存记忆**：自动保存当前学习记录到文件
8. **循环迭代**：直到达到及格分数或最大迭代次数

## 核心功能

### 老师Agent (`teacher_agent.py`)
- 从配置文件加载考点
- 评估学生答案并打分
- 提供详细的改进建议

### 学生Agent (`student_agent.py`)
- 学习考点知识
- 参加考试答题
- 根据反馈迭代优化prompt
- 自动保存和加载学习记忆
- 支持从配置动态创建

### 搜索工具 (`search_tool.py`)
- 使用 DuckDuckGo 搜索引擎（无需API Key）
- 自动搜索与考点相关的信息
- 将搜索结果提供给学生Agent辅助学习

### 记忆管理 (`memory.py`)
- 将学习记录保存为 Markdown 文件（人类可读）
- 同时保存为 JSON 文件（方便加载）
- 每次学习后自动保存
- 启动时自动加载之前的记忆

## 配置文件说明 (`config.yaml`)

### 模型配置
```yaml
model:
  name: "gpt-3.5-turbo"  # 模型名称
```

### 搜索配置
```yaml
search:
  enabled: true           # 是否启用搜索功能
  num_results: 5       # 每次搜索返回的结果数量
```

### 记忆配置
```yaml
memory:
  enabled: true          # 是否启用记忆功能
  auto_save: true        # 是否自动保存记忆
  memory_dir: "memory"  # 记忆文件存储目录
```

### 学生配置
```yaml
students:
  - name: "学生名称"              # 显示名称
    role: "角色描述"              # 角色身份
    prompt_file: "路径"         # prompt文件保存路径
    default_prompt: |          # 默认prompt
      多行prompt内容...
    config_key: "key"         # 对应考点配置的key
```

### 考点配置
```yaml
config_key:  # 对应学生配置中的config_key
  role: "角色描述"
  max_iterations: 3
  topics:
    - name: "考点名称"
      description: "考点描述"
      questions:
        - "问题1"
        - "问题2"
```

## 记忆文件说明

记忆会保存在 `memory/` 目录下，每个学生一个文件：
- `学生名_memory.md` - 人类可读的格式
- `学生名_memory.json` - 结构化格式，用于下次启动时自动加载

## 如何添加新学生

1. 在 `config.yaml` 的 `students` 列表中添加新学生配置
2. 在 `config.yaml` 中添加对应的考点配置（使用相同的 `config_key`）
3. 运行程序，新学生就会自动出现在列表中！
