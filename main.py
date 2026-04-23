import os
import sys
from teacher_agent import TeacherAgent
from student_agent import create_all_students
from config import Config
from services.dataset_content_store import DatasetContentStore
from services.douyin_media_store import DouyinMediaStore
from services.douyin_resolver import DouyinResolver
from services.douyin_understander import DouyinUnderstander


base_dir = os.path.dirname(os.path.abspath(__file__))
dataset_store = DatasetContentStore(base_dir)
douyin_resolver = DouyinResolver()
douyin_media_store = DouyinMediaStore(dataset_store)
douyin_understander = DouyinUnderstander(Config().model_name)


def print_separator(title=""):
    print("\n" + "="*80)
    if title:
        print(f"  {title}")
        print("="*80)


def train_student(student, teacher, role_config):
    role = role_config["role"]
    max_iterations = role_config["max_iterations"]
    topics = role_config["topics"]

    print_separator(f"开始训练 {student.name}")

    for topic in topics:
        task = teacher.assign_task(topic)
        print(f"\n📚 考点：{task['topic_name']}")
        print(f"   描述：{task['description']}")

        last_feedback = None

        for iteration in range(1, max_iterations + 1):
            print_separator(f"第 {iteration} 轮学习 - {task['topic_name']}")

            print("🔍 学生正在学习...")
            knowledge = student.learn(task['description'], last_feedback)
            print(f"   学习完成!")

            for question in task['questions']:
                print(f"\n📝 考题：{question}")
                print("💬 学生正在回答...")
                answer = student.take_exam(question)
                print(f"   回答：\n{answer}")

                print("\n👨‍🏫 老师正在评估...")
                evaluation = teacher.evaluate_answer(question, answer, role)
                print(f"   评估：\n{evaluation}")

                score = teacher.extract_score(evaluation)
                print(f"\n⭐ 得分：{score}分")

                if teacher.is_pass(score):
                    print("✅ 通过!")
                    feedback = teacher.give_feedback(evaluation)
                    last_feedback = feedback
                    break
                else:
                    print("❌ 未通过，需要改进...")
                    feedback = teacher.give_feedback(evaluation)
                    last_feedback = feedback

            print("\n🔄 学生正在根据反馈优化prompt...")
            new_prompt = student.iterate_prompt(last_feedback)
            print("   Prompt已更新!")

    print_separator(f"{student.name} 训练完成!")


def case_study_train(student, case_studies, config_key):
    """案例纠偏训练模式"""
    print_separator(f"开始案例纠偏训练：{student.name}")

    if not case_studies:
        print("⚠️  没有找到案例数据！")
        return

    print(f"\n📋 找到 {len(case_studies)} 个案例\n")

    for idx, case in enumerate(case_studies, 1):
        title = case.get('title') or f"GID: {case.get('gid', '未知')}"
        content = case.get('content', '')
        resolved = dataset_store.load_extract(config_key, case.get('gid', '')) if case.get('gid') else None
        understanding = dataset_store.load_understanding(config_key, case.get('gid', '')) if case.get('gid') else None

        if not resolved and case.get('gid'):
            try:
                resolved = douyin_resolver.resolve_gid(case.get('gid'))
                dataset_store.save_extract(config_key, case.get('gid'), resolved)
            except Exception as exc:
                print_separator(f"案例 {idx}")
                print(f"\n🧾 GID: {case.get('gid', '未知')}")
                print(f"⚠️  通过 gid 拉取内容失败：{exc}")
                continue

        if not understanding and resolved and case.get('gid'):
            try:
                material_bundle = douyin_media_store.ensure_local_assets(config_key, case.get('gid'), resolved)
                understanding = douyin_understander.understand(resolved, material_bundle=material_bundle)
                dataset_store.save_understanding(config_key, case.get('gid'), understanding)
            except Exception as exc:
                print_separator(f"案例 {idx}")
                print(f"\n🧾 GID: {case.get('gid', '未知')}")
                print(f"⚠️  豆包理解失败，将回退到基础解析内容：{exc}")

        if understanding and understanding.get('parsed'):
            parsed = understanding['parsed']
            title = parsed.get('title') or title
            content = parsed.get('content', '') or content
        elif resolved:
            title = resolved.get('title') or title
            content = resolved.get('content', '') or content

        if not content:
            print_separator(f"案例 {idx}")
            print(f"\n🧾 案例缺少 title/content，且没有可用 gid：{case.get('gid', '未知')}")
            continue

        print_separator(f"案例 {idx}")
        print(f"\n📝 案例标题：{title}")
        print(f"📄 案例内容：{content}")

        # 学生先判断
        print("\n🤖 学生正在判断...")
        student_judgment = student.judge_case(title, content)
        print(f"   学生判断：\n{student_judgment}")

        # 展示用户的正确判断
        print(f"\n👤 用户的正确判断：")
        print(f"   判断：{case['user_judgment']}")
        print(f"   理由：{case['user_reason']}")

        # 询问是否继续
        input("\n按回车键继续，让学生学习纠偏...")

        # 学生学习纠偏
        print("\n📚 学生正在学习纠偏...")
        knowledge = student.learn_from_user_feedback(
            title, content,
            case['user_judgment'], case['user_reason'],
            student_judgment
        )
        print(f"   学习完成！")
        print(f"   学习总结：\n{knowledge}")

        # 优化 prompt
        print("\n🔄 学生正在优化prompt...")
        feedback = f"用户判断：{case['user_judgment']}，理由：{case['user_reason']}\n学生判断：{student_judgment}"
        new_prompt = student.iterate_prompt(feedback)
        print("   Prompt已更新!")

    print_separator(f"案例纠偏训练完成：{student.name}!")


def main():
    config = Config()
    teacher = TeacherAgent()

    # 从配置创建所有学生
    all_students = create_all_students()

    if not all_students:
        print("❌ 没有找到学生Agent配置！")
        return

    print("🎓 Agent训练系统启动")
    print("="*80)
    print(f"\n找到 {len(all_students)} 个学生Agent：")
    for i, (config_key, student) in enumerate(all_students, 1):
        print(f"{i}. {student.name} ({student.role})")

    print("\n请选择训练模式：")
    print("1. 常规考点训练")
    print("2. 案例纠偏训练")

    try:
        mode_choice = int(input("\n请输入模式选项：").strip())

        print("\n请选择要训练的学生Agent：")
        for i, (config_key, student) in enumerate(all_students, 1):
            print(f"{i}. {student.name}")

        student_choice = int(input("\n请输入学生选项：").strip()) - 1

        if 0 <= student_choice < len(all_students):
            config_key, student = all_students[student_choice]

            if mode_choice == 1:
                # 常规考点训练
                role_config = config.get_student_config(config_key)
                if role_config:
                    train_student(student, teacher, role_config)
                else:
                    print(f"⚠️ 未找到学生 {student.name} 的考点配置！")
            elif mode_choice == 2:
                # 案例纠偏训练
                case_studies = config.get_case_studies(config_key)
                case_study_train(student, case_studies, config_key)
            else:
                print("无效选项！")
                return
        else:
            print("无效选项！")
            return

        print_separator("所有训练完成!")

    except ValueError:
        print("请输入有效的数字！")


if __name__ == "__main__":
    main()
