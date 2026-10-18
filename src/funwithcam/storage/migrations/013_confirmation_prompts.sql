UPDATE speech_intents
SET confirmation_prompt = CASE command
        WHEN 'start' THEN
            '你是要开始学习吗？请说“确认开始”，或者说“取消”。'
        WHEN 'pause' THEN
            '你是要暂停学习吗？请说“确认暂停”，或者说“继续学习”。'
        WHEN 'resume' THEN
            '你是要继续学习吗？请说“确认继续”，或者说“结束学习”。'
        WHEN 'finish' THEN
            '准备结束本次学习了吗？请说“检查完成情况”，或者说“继续学习”。'
        WHEN 'start_checkin' THEN
            '你是要开始视频打卡吗？请说“确认打卡”，或者说“取消”。'
    END,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE (command = 'start' AND confirmation_prompt =
        '你是要开始学习吗？请回答“确认开始”或者“取消”。')
   OR (command = 'pause' AND confirmation_prompt =
        '你是要暂停学习吗？请回答“确认暂停”或者“继续学习”。')
   OR (command = 'resume' AND confirmation_prompt =
        '你是要继续学习吗？请回答“确认继续”或者“结束学习”。')
   OR (command = 'finish' AND confirmation_prompt =
        '准备结束本次学习了吗？请回答“检查完成情况”或者“继续学习”。')
   OR (command = 'start_checkin' AND confirmation_prompt =
        '你是要开始视频打卡吗？请回答“确认打卡”或者“取消”。');
