import threading
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
        self._lock = threading.Lock()

    def start_standard_training(self, config_key):
        self._refresh_runtime()
        student = self._get_student(config_key)
        role_config = self.config.get_student_config(config_key)
        if not student or not role_config:
            raise ValueError(f"未找到 student 或训练配置：{config_key}")

        total_units = self._estimate_standard_units(role_config)
        run = self._build_run_skeleton(config_key, student.name, "standard", total_units=total_units)
        self._save_run(run)
        self._emit_event(run["run_id"], {
            "event": "run_started",
            "speaker": "system",
            "phase": "run",
            "content": f"开始常规训练：{student.name}",
        })

        worker = threading.Thread(
            target=self._run_standard_training_sync,
            args=(run["run_id"], config_key),
            daemon=True,
        )
        worker.start()
        return run

    def _run_standard_training_sync(self, run_id, config_key):
        self._refresh_runtime()
        student = self._get_student(config_key)
        teacher = TeacherAgent(self.base_dir / "config.yaml")
        role_config = self.config.get_student_config(config_key)
        run = self.run_store.get(run_id)
        if not student or not role_config or not run:
            if run:
                run["status"] = "failed"
                run["error"] = f"未找到 student 或训练配置：{config_key}"
                self._finalize_run(run, [])
            return

        question_scores = []

        try:
            for topic in role_config.get("topics", []):
                self._emit_event(run_id, {
                    "event": "step",
                    "speaker": "system",
                    "phase": "topic",
                    "topic_name": topic.get("name", ""),
                    "content": f"开始考点：{topic.get('name', '')}",
                })
                self._set_current_step(run, f"开始考点：{topic.get('name', '')}")
                topic_record = {
                    "topic_name": topic.get("name", ""),
                    "description": topic.get("description", ""),
                    "iterations": [],
                }
                last_feedback = None

                for iteration in range(1, role_config.get("max_iterations", 1) + 1):
                    self._set_current_step(run, f"学生学习中：{topic.get('name', '')} 第 {iteration} 轮")
                    self._emit_event(run_id, {
                        "event": "message_started",
                        "speaker": "student",
                        "phase": "learn",
                        "topic_name": topic.get("name", ""),
                        "iteration": iteration,
                        "content": "",
                    })
                    knowledge = student.learn(
                        topic.get("description", ""),
                        last_feedback,
                        on_delta=lambda delta, rid=run_id, t=topic.get("name", ""), i=iteration: self._emit_event(rid, {
                            "event": "message_delta",
                            "speaker": "student",
                            "phase": "learn",
                            "topic_name": t,
                            "iteration": i,
                            "content": delta,
                        }),
                    )
                    self._emit_event(run_id, {
                        "event": "message_completed",
                        "speaker": "student",
                        "phase": "learn",
                        "topic_name": topic.get("name", ""),
                        "iteration": iteration,
                        "content": knowledge,
                    })
                    self._advance_progress(run, f"完成学习：{topic.get('name', '')} 第 {iteration} 轮")
                    iteration_record = {
                        "iteration": iteration,
                        "knowledge": knowledge,
                        "questions": [],
                    }
                    self._save_run(run)

                    for question in topic.get("questions", []):
                        self._set_current_step(run, f"学生答题中：{question}")
                        self._emit_event(run_id, {
                            "event": "message_started",
                            "speaker": "student",
                            "phase": "answer",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": "",
                        })
                        answer = student.take_exam(
                            question,
                            on_delta=lambda delta, rid=run_id, t=topic.get("name", ""), i=iteration, q=question: self._emit_event(rid, {
                                "event": "message_delta",
                                "speaker": "student",
                                "phase": "answer",
                                "topic_name": t,
                                "iteration": i,
                                "question": q,
                                "content": delta,
                            }),
                        )
                        self._emit_event(run_id, {
                            "event": "message_completed",
                            "speaker": "student",
                            "phase": "answer",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": answer,
                        })
                        self._advance_progress(run, f"完成答题：{question}")

                        self._set_current_step(run, f"老师评估中：{question}")
                        self._emit_event(run_id, {
                            "event": "message_started",
                            "speaker": "teacher",
                            "phase": "evaluation",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": "",
                        })
                        evaluation = teacher.evaluate_answer(
                            question,
                            answer,
                            role_config.get("role", student.role),
                            on_delta=lambda delta, rid=run_id, t=topic.get("name", ""), i=iteration, q=question: self._emit_event(rid, {
                                "event": "message_delta",
                                "speaker": "teacher",
                                "phase": "evaluation",
                                "topic_name": t,
                                "iteration": i,
                                "question": q,
                                "content": delta,
                            }),
                        )
                        self._emit_event(run_id, {
                            "event": "message_completed",
                            "speaker": "teacher",
                            "phase": "evaluation",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": evaluation,
                        })
                        self._advance_progress(run, f"完成评估：{question}")
                        score = teacher.extract_score(evaluation)
                        self._emit_event(run_id, {
                            "event": "step",
                            "speaker": "system",
                            "phase": "score",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": f"老师评分：{score} 分",
                        })

                        self._set_current_step(run, f"老师反馈中：{question}")
                        self._emit_event(run_id, {
                            "event": "message_started",
                            "speaker": "teacher",
                            "phase": "feedback",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": "",
                        })
                        feedback = teacher.give_feedback(
                            evaluation,
                            on_delta=lambda delta, rid=run_id, t=topic.get("name", ""), i=iteration, q=question: self._emit_event(rid, {
                                "event": "message_delta",
                                "speaker": "teacher",
                                "phase": "feedback",
                                "topic_name": t,
                                "iteration": i,
                                "question": q,
                                "content": delta,
                            }),
                        )
                        self._emit_event(run_id, {
                            "event": "message_completed",
                            "speaker": "teacher",
                            "phase": "feedback",
                            "topic_name": topic.get("name", ""),
                            "iteration": iteration,
                            "question": question,
                            "content": feedback,
                        })
                        self._advance_progress(run, f"完成反馈：{question}")
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
                        self._save_run(run)

                        if passed:
                            self._emit_event(run_id, {
                                "event": "step",
                                "speaker": "system",
                                "phase": "pass",
                                "topic_name": topic.get("name", ""),
                                "iteration": iteration,
                                "question": question,
                                "content": "已达到通过分数，进入下一步。",
                            })
                            break

                    self._set_current_step(run, f"Prompt 优化中：{topic.get('name', '')} 第 {iteration} 轮")
                    self._emit_event(run_id, {
                        "event": "message_started",
                        "speaker": "student",
                        "phase": "prompt_iteration",
                        "topic_name": topic.get("name", ""),
                        "iteration": iteration,
                        "content": "",
                    })
                    new_prompt = student.iterate_prompt(
                        last_feedback or "请继续优化回答质量",
                        on_delta=lambda delta, rid=run_id, t=topic.get("name", ""), i=iteration: self._emit_event(rid, {
                            "event": "message_delta",
                            "speaker": "student",
                            "phase": "prompt_iteration",
                            "topic_name": t,
                            "iteration": i,
                            "content": delta,
                        }),
                    )
                    self._emit_event(run_id, {
                        "event": "message_completed",
                        "speaker": "student",
                        "phase": "prompt_iteration",
                        "topic_name": topic.get("name", ""),
                        "iteration": iteration,
                        "content": new_prompt,
                    })
                    self._advance_progress(run, f"完成 prompt 优化：{topic.get('name', '')} 第 {iteration} 轮")
                    iteration_record["prompt_after_iteration"] = new_prompt
                    topic_record["iterations"].append(iteration_record)
                    self._save_run(run)

                run["topics"].append(topic_record)
                self._save_run(run)

            run["status"] = "completed"
        except Exception as exc:
            run["status"] = "failed"
            run["error"] = str(exc)
            self._emit_event(run_id, {
                "event": "run_failed",
                "speaker": "system",
                "phase": "run",
                "content": str(exc),
            })
        finally:
            self._finalize_run(run, question_scores)

        return run

    def start_case_training(self, config_key):
        self._refresh_runtime()
        student = self._get_student(config_key)
        case_studies = self.config.get_case_studies(config_key)
        if not student:
            raise ValueError(f"未找到 student：{config_key}")

        total_units = self._estimate_case_units(case_studies)
        run = self._build_run_skeleton(config_key, student.name, "case", total_units=total_units)
        self._save_run(run)
        self._emit_event(run["run_id"], {
            "event": "run_started",
            "speaker": "system",
            "phase": "run",
            "content": f"开始案例纠偏：{student.name}",
        })

        worker = threading.Thread(
            target=self._run_case_training_sync,
            args=(run["run_id"], config_key),
            daemon=True,
        )
        worker.start()
        return run

    def _run_case_training_sync(self, run_id, config_key):
        self._refresh_runtime()
        student = self._get_student(config_key)
        case_studies = self.config.get_case_studies(config_key)
        run = self.run_store.get(run_id)
        if not student or not run:
            if run:
                run["status"] = "failed"
                run["error"] = f"未找到 student：{config_key}"
                self._finalize_case_run(run, 0, 0, 0)
            return

        processed = 0
        skipped = 0

        try:
            for index, case in enumerate(case_studies, start=1):
                self._emit_event(run_id, {
                    "event": "step",
                    "speaker": "system",
                    "phase": "case",
                    "case_index": index,
                    "content": f"开始处理案例 {index}",
                })
                self._set_current_step(run, f"处理案例 {index}/{len(case_studies)}")
                title = case.get("title") or f"GID: {case.get('gid', '未知')}"
                content = case.get("content", "")
                resolved_content = self.dataset_store.load_extract(config_key, case.get("gid", "")) if case.get("gid") else None
                understanding = self.dataset_store.load_understanding(config_key, case.get("gid", "")) if case.get("gid") else None

                if not resolved_content and case.get("gid"):
                    try:
                        resolved_content = self.douyin_resolver.resolve_gid(case.get("gid"))
                        self.dataset_store.save_extract(config_key, case.get("gid"), resolved_content)
                        self._advance_progress(run, f"完成基础解析：案例 {index}")
                    except Exception as exc:
                        resolved_content = {"error": str(exc)}

                if not understanding and resolved_content and not resolved_content.get("error") and case.get("gid"):
                    try:
                        self._set_current_step(run, f"内容理解中：案例 {index}")
                        material_bundle = self.douyin_media_store.ensure_local_assets(config_key, case.get("gid"), resolved_content)
                        understanding = self.douyin_understander.understand(resolved_content, material_bundle=material_bundle)
                        self.dataset_store.save_understanding(config_key, case.get("gid"), understanding)
                        self._advance_progress(run, f"完成内容理解：案例 {index}")
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
                    self._save_run(run)
                    continue

                self._set_current_step(run, f"学生判断中：案例 {index}")
                self._emit_event(run_id, {
                    "event": "message_started",
                    "speaker": "student",
                    "phase": "judge",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": "",
                })
                student_judgment = student.judge_case(
                    title,
                    content,
                    on_delta=lambda delta, rid=run_id, idx=index, gid=case.get("gid", ""): self._emit_event(rid, {
                        "event": "message_delta",
                        "speaker": "student",
                        "phase": "judge",
                        "case_index": idx,
                        "gid": gid,
                        "content": delta,
                    }),
                )
                self._emit_event(run_id, {
                    "event": "message_completed",
                    "speaker": "student",
                    "phase": "judge",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": student_judgment,
                })
                self._advance_progress(run, f"完成学生判定：案例 {index}")
                self._emit_event(run_id, {
                    "event": "step",
                    "speaker": "system",
                    "phase": "user_reference",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": f"人工标准：{case.get('user_judgment', '')}；理由：{case.get('user_reason', '')}",
                })
                self._set_current_step(run, f"学生纠偏学习中：案例 {index}")
                self._emit_event(run_id, {
                    "event": "message_started",
                    "speaker": "student",
                    "phase": "reflection",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": "",
                })
                knowledge = student.learn_from_user_feedback(
                    title,
                    content,
                    case.get("user_judgment", ""),
                    case.get("user_reason", ""),
                    student_judgment,
                    on_delta=lambda delta, rid=run_id, idx=index, gid=case.get("gid", ""): self._emit_event(rid, {
                        "event": "message_delta",
                        "speaker": "student",
                        "phase": "reflection",
                        "case_index": idx,
                        "gid": gid,
                        "content": delta,
                    }),
                )
                self._emit_event(run_id, {
                    "event": "message_completed",
                    "speaker": "student",
                    "phase": "reflection",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": knowledge,
                })
                self._advance_progress(run, f"完成纠偏学习：案例 {index}")
                self._set_current_step(run, f"Prompt 优化中：案例 {index}")
                self._emit_event(run_id, {
                    "event": "message_started",
                    "speaker": "student",
                    "phase": "prompt_iteration",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": "",
                })
                prompt = student.iterate_prompt(
                    f"用户判断：{case.get('user_judgment', '')}，理由：{case.get('user_reason', '')}\n学生判断：{student_judgment}",
                    on_delta=lambda delta, rid=run_id, idx=index, gid=case.get("gid", ""): self._emit_event(rid, {
                        "event": "message_delta",
                        "speaker": "student",
                        "phase": "prompt_iteration",
                        "case_index": idx,
                        "gid": gid,
                        "content": delta,
                    }),
                )
                self._emit_event(run_id, {
                    "event": "message_completed",
                    "speaker": "student",
                    "phase": "prompt_iteration",
                    "case_index": index,
                    "gid": case.get("gid", ""),
                    "content": prompt,
                })
                self._advance_progress(run, f"完成 prompt 优化：案例 {index}")

                case_record.update({
                    "status": "completed",
                    "student_judgment": student_judgment,
                    "knowledge": knowledge,
                    "prompt_after_iteration": prompt,
                })
                processed += 1
                run["cases"].append(case_record)
                self._save_run(run)

            run["status"] = "completed"
        except Exception as exc:
            run["status"] = "failed"
            run["error"] = str(exc)
            self._emit_event(run_id, {
                "event": "run_failed",
                "speaker": "system",
                "phase": "run",
                "content": str(exc),
            })
        finally:
            self._finalize_case_run(run, processed, skipped, len(case_studies))

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

    def _build_run_skeleton(self, config_key, student_name, mode, total_units=1):
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
            "progress_percent": 0,
            "current_step": "等待开始",
            "progress": {
                "completed_units": 0,
                "total_units": max(1, total_units),
            },
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
        run["progress_percent"] = 100
        run["current_step"] = "训练完成" if run.get("status") == "completed" else run.get("current_step", "")
        self._emit_event(run["run_id"], {
            "event": "run_completed" if run.get("status") == "completed" else "run_finished",
            "speaker": "system",
            "phase": "run",
            "content": run["current_step"] or "训练结束",
        })
        self._save_run(run)

    def _finalize_case_run(self, run, processed, skipped, total_cases):
        run["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run["summary"] = {
            "processed_cases": processed,
            "skipped_cases": skipped,
            "total_cases": total_cases,
        }
        run["progress_percent"] = 100
        run["current_step"] = "案例纠偏完成" if run.get("status") == "completed" else run.get("current_step", "")
        self._emit_event(run["run_id"], {
            "event": "run_completed" if run.get("status") == "completed" else "run_finished",
            "speaker": "system",
            "phase": "run",
            "content": run["current_step"] or "案例纠偏结束",
        })
        self._save_run(run)

    def _save_run(self, run):
        self.run_store.save(run)

    def _emit_event(self, run_id, payload):
        event = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **payload,
        }
        self.run_store.append_event(run_id, event)

    def _set_current_step(self, run, label):
        run["current_step"] = label
        self._save_run(run)

    def _advance_progress(self, run, label):
        progress = run.setdefault("progress", {"completed_units": 0, "total_units": 1})
        progress["completed_units"] += 1
        total_units = max(1, progress.get("total_units", 1))
        run["progress_percent"] = min(99, round(progress["completed_units"] / total_units * 100, 1))
        run["current_step"] = label
        self._save_run(run)

    def _estimate_standard_units(self, role_config):
        total = 0
        max_iterations = role_config.get("max_iterations", 1)
        for topic in role_config.get("topics", []):
            question_count = len(topic.get("questions", []))
            total += max_iterations * (1 + question_count * 3 + 1)
        return max(1, total)

    def _estimate_case_units(self, case_studies):
        total = 0
        for case in case_studies:
            total += 3
            if case.get("gid"):
                total += 2
        return max(1, total)
