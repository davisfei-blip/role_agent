from datetime import datetime
from pathlib import Path

from config import Config
from services.dataset_content_store import DatasetContentStore
from services.douyin_media_store import DouyinMediaStore
from teacher_agent import TeacherAgent, refresh_runtime_state as refresh_teacher_state
from student_agent import create_all_students, refresh_runtime_state as refresh_student_state
from services.douyin_resolver import DouyinResolver
from services.douyin_understander import DouyinUnderstander
from services.run_store import RunStore


class TrainingRunner:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.config = Config(self.base_dir / "config.yaml")
        self.run_store = RunStore(self.base_dir)
        self.dataset_store = DatasetContentStore(self.base_dir)
        self.douyin_resolver = DouyinResolver()
        self.douyin_media_store = DouyinMediaStore(self.dataset_store)
        self.douyin_understander = DouyinUnderstander(self.config.model_name)

    def run_standard_training(self, config_key):
        self._refresh_runtime()
        student = self._get_student(config_key)
        teacher = TeacherAgent(self.base_dir / "config.yaml")
        role_config = self.config.get_student_config(config_key)
        if not student or not role_config:
            raise ValueError(f"未找到 student 或训练配置：{config_key}")

        run = self._build_run_skeleton(config_key, student.name, "standard")
        question_scores = []

        try:
            for topic in role_config.get("topics", []):
                topic_record = {
                    "topic_name": topic.get("name", ""),
                    "description": topic.get("description", ""),
                    "iterations": [],
                }
                last_feedback = None

                for iteration in range(1, role_config.get("max_iterations", 1) + 1):
                    knowledge = student.learn(topic.get("description", ""), last_feedback)
                    iteration_record = {
                        "iteration": iteration,
                        "knowledge": knowledge,
                        "questions": [],
                    }

                    for question in topic.get("questions", []):
                        answer = student.take_exam(question)
                        evaluation = teacher.evaluate_answer(question, answer, role_config.get("role", student.role))
                        score = teacher.extract_score(evaluation)
                        feedback = teacher.give_feedback(evaluation)
                        passed = teacher.is_pass(score)
                        last_feedback = feedback
                        question_scores.append(score)

                        iteration_record["questions"].append({
                            "question": question,
                            "answer": answer,
                            "evaluation": evaluation,
                            "score": score,
                            "passed": passed,
                            "feedback": feedback,
                        })

                        if passed:
                            break

                    new_prompt = student.iterate_prompt(last_feedback or "请继续优化回答质量")
                    iteration_record["prompt_after_iteration"] = new_prompt
                    topic_record["iterations"].append(iteration_record)

                run["topics"].append(topic_record)

            run["status"] = "completed"
        except Exception as exc:
            run["status"] = "failed"
            run["error"] = str(exc)
            raise
        finally:
            self._finalize_run(run, question_scores)

        return run

    def run_case_training(self, config_key):
        self._refresh_runtime()
        student = self._get_student(config_key)
        case_studies = self.config.get_case_studies(config_key)
        if not student:
            raise ValueError(f"未找到 student：{config_key}")

        run = self._build_run_skeleton(config_key, student.name, "case")
        processed = 0
        skipped = 0

        try:
            for index, case in enumerate(case_studies, start=1):
                title = case.get("title") or f"GID: {case.get('gid', '未知')}"
                content = case.get("content", "")
                resolved_content = self.dataset_store.load_extract(config_key, case.get("gid", "")) if case.get("gid") else None
                understanding = self.dataset_store.load_understanding(config_key, case.get("gid", "")) if case.get("gid") else None

                if not resolved_content and case.get("gid"):
                    try:
                        resolved_content = self.douyin_resolver.resolve_gid(case.get("gid"))
                        self.dataset_store.save_extract(config_key, case.get("gid"), resolved_content)
                    except Exception as exc:
                        resolved_content = {"error": str(exc)}

                if not understanding and resolved_content and not resolved_content.get("error") and case.get("gid"):
                    try:
                        material_bundle = self.douyin_media_store.ensure_local_assets(config_key, case.get("gid"), resolved_content)
                        understanding = self.douyin_understander.understand(resolved_content, material_bundle=material_bundle)
                        self.dataset_store.save_understanding(config_key, case.get("gid"), understanding)
                    except Exception as exc:
                        understanding = {"status": "failed", "error": str(exc)}

                if understanding and understanding.get("parsed"):
                    parsed = understanding["parsed"]
                    title = parsed.get("title") or title
                    content = parsed.get("content") or content
                elif resolved_content and not resolved_content.get("error"):
                    title = resolved_content.get("title") or title
                    content = resolved_content.get("content", "") or content

                case_record = {
                    "index": index,
                    "gid": case.get("gid", ""),
                    "title": title,
                    "user_judgment": case.get("user_judgment", ""),
                    "user_reason": case.get("user_reason", ""),
                    "resolved_content": resolved_content,
                    "understanding": understanding,
                }

                if not content:
                    case_record["status"] = "skipped"
                    case_record["reason"] = (
                        (resolved_content or {}).get("error")
                        or "当前案例只有 gid，且未能成功拉取对应内容。"
                    )
                    skipped += 1
                    run["cases"].append(case_record)
                    continue

                student_judgment = student.judge_case(title, content)
                knowledge = student.learn_from_user_feedback(
                    title,
                    content,
                    case.get("user_judgment", ""),
                    case.get("user_reason", ""),
                    student_judgment,
                )
                prompt = student.iterate_prompt(
                    f"用户判断：{case.get('user_judgment', '')}，理由：{case.get('user_reason', '')}\n学生判断：{student_judgment}"
                )

                case_record.update({
                    "status": "completed",
                    "student_judgment": student_judgment,
                    "knowledge": knowledge,
                    "prompt_after_iteration": prompt,
                })
                processed += 1
                run["cases"].append(case_record)

            run["status"] = "completed"
        except Exception as exc:
            run["status"] = "failed"
            run["error"] = str(exc)
            raise
        finally:
            run["summary"] = {
                "processed_cases": processed,
                "skipped_cases": skipped,
                "total_cases": len(case_studies),
            }
            self._save_run(run)

        return run

    def _refresh_runtime(self):
        self.config.reload()
        refresh_student_state()
        refresh_teacher_state()

    def _get_student(self, config_key):
        for key, student in create_all_students():
            if key == config_key:
                return student
        return None

    def _build_run_skeleton(self, config_key, student_name, mode):
        return {
            "run_id": self.run_store.create_run_id(config_key, mode),
            "config_key": config_key,
            "student_name": student_name,
            "mode": mode,
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "topics": [],
            "cases": [],
            "summary": {},
            "error": None,
        }

    def _finalize_run(self, run, question_scores):
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if question_scores:
            run["summary"] = {
                "question_count": len(question_scores),
                "average_score": round(sum(question_scores) / len(question_scores), 2),
                "latest_score": question_scores[-1],
            }
        else:
            run["summary"] = {
                "question_count": 0,
                "average_score": None,
                "latest_score": None,
            }
        self._save_run(run)

    def _save_run(self, run):
        if not run.get("finished_at"):
            run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.run_store.save(run)
