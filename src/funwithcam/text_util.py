def normalize_speech_text(text: str) -> str:
    """语音识别文本归一化：小写并去掉中文/英文标点。"""
    return "".join(
        character
        for character in text.lower()
        if character not in " ，。！？,.!?、\t\r\n"
    )
