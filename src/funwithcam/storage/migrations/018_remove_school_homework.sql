-- 移除内置「学校作业」模板：日程页作业列以数学/语文/英语/其他作业代替。
-- 级联清理 template_schedule_dates / template_schedule_overrides 中的引用。
DELETE FROM task_types WHERE task_key = 'school_homework';
