"""古诗解读：DeepSeek 在线优先，失败降级到内置离线诗库。

数据模型（内置与 API 返回统一）::

    {
        "title": "静夜思",
        "dynasty": "唐",
        "author": "李白",
        "segments": [
            {"original": "床前明月光，疑是地上霜。",
             "translation": "明亮的月光洒在床前，好像地上结了一层霜。"},
            ...
        ],
        "meaning": "这首诗借月亮，表达了诗人对家乡的思念之情。",
    }
"""

import json
import logging
import os
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import PoemConfig
from .text_util import normalize_speech_text


LOGGER = logging.getLogger(__name__)

DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"

# 解读结果的敏感词过滤：命中任一即视为不可播报，降级到诗库。
_POEM_BLOCKED_TERMS = (
    "自杀", "赌博", "毒品", "黄色", "色情", "成人",
)

# 内置离线诗库（小学必背）。API 未配置或失败时的兜底来源。
POEM_LIBRARY: list[dict] = [
    {
        "title": "静夜思",
        "dynasty": "唐",
        "author": "李白",
        "segments": [
            {
                "original": "床前明月光，疑是地上霜。",
                "translation": "明亮的月光洒在床前，好像地上结了一层霜。",
            },
            {
                "original": "举头望明月，低头思故乡。",
                "translation": "抬起头望着天上的月亮，低下头，思念起远方的家乡。",
            },
        ],
        "meaning": "这首诗借月色，表达了诗人对家乡的思念之情。",
    },
    {
        "title": "春晓",
        "dynasty": "唐",
        "author": "孟浩然",
        "segments": [
            {
                "original": "春眠不觉晓，处处闻啼鸟。",
                "translation": "春天的夜里睡得香甜，不知不觉天都亮了，到处都能听到鸟儿的叫声。",
            },
            {
                "original": "夜来风雨声，花落知多少。",
                "translation": "昨夜听到刮风下雨的声音，不知道有多少花儿被吹落。",
            },
        ],
        "meaning": "这首诗描写春天早晨的景色，表达诗人对春天的喜爱和对时光的珍惜。",
    },
    {
        "title": "咏鹅",
        "dynasty": "唐",
        "author": "骆宾王",
        "segments": [
            {
                "original": "鹅，鹅，鹅，曲项向天歌。",
                "translation": "大白鹅呀大白鹅，弯着脖子对着天空唱歌。",
            },
            {
                "original": "白毛浮绿水，红掌拨清波。",
                "translation": "白色的羽毛漂浮在碧绿的水面上，红色的脚掌划动着清清的波浪。",
            },
        ],
        "meaning": "这首诗生动地描写了大白鹅的外形和在水里游动的姿态，表现了作者对鹅的喜爱。",
    },
    {
        "title": "登鹳雀楼",
        "dynasty": "唐",
        "author": "王之涣",
        "segments": [
            {
                "original": "白日依山尽，黄河入海流。",
                "translation": "太阳靠着远处的山慢慢落下，黄河水滚滚流向大海。",
            },
            {
                "original": "欲穷千里目，更上一层楼。",
                "translation": "要想看到更远的地方，就要再登上一层楼。",
            },
        ],
        "meaning": "这首诗前两句写壮丽的景色，后两句告诉我们：站得高才能看得远，鼓励我们不断努力。",
    },
    {
        "title": "望庐山瀑布",
        "dynasty": "唐",
        "author": "李白",
        "segments": [
            {
                "original": "日照香炉生紫烟，遥看瀑布挂前川。",
                "translation": "太阳照在香炉峰上，升起紫色的烟雾，远远看去瀑布像挂在山前的一条大河。",
            },
            {
                "original": "飞流直下三千尺，疑是银河落九天。",
                "translation": "飞泻而下的水流好像有三千尺长，让人怀疑是银河从天上落了下来。",
            },
        ],
        "meaning": "这首诗用夸张的手法描写庐山瀑布的壮观，表达了诗人对祖国山河的赞美。",
    },
    {
        "title": "悯农（其二）",
        "dynasty": "唐",
        "author": "李绅",
        "aliases": ["悯农"],
        "segments": [
            {
                "original": "锄禾日当午，汗滴禾下土。",
                "translation": "中午太阳最晒的时候，农民还在锄地，汗水一滴滴落在禾苗下的泥土里。",
            },
            {
                "original": "谁知盘中餐，粒粒皆辛苦。",
                "translation": "有谁知道盘子里的每一粒饭，都凝聚着农民辛苦的汗水。",
            },
        ],
        "meaning": "这首诗告诉我们要珍惜粮食，体会农民劳动的辛苦。",
    },
    {
        "title": "绝句",
        "dynasty": "唐",
        "author": "杜甫",
        "segments": [
            {
                "original": "两个黄鹂鸣翠柳，一行白鹭上青天。",
                "translation": "两只黄鹂在翠绿的柳树上唱歌，一行白鹭飞上了蓝蓝的天空。",
            },
            {
                "original": "窗含西岭千秋雪，门泊东吴万里船。",
                "translation": "窗外是西岭上千年不化的积雪，门前停着从万里之外东吴驶来的船。",
            },
        ],
        "meaning": "这首诗描写了春天的明媚景色，表达了诗人内心的喜悦。",
    },
    {
        "title": "江雪",
        "dynasty": "唐",
        "author": "柳宗元",
        "segments": [
            {
                "original": "千山鸟飞绝，万径人踪灭。",
                "translation": "所有的山上都看不到飞鸟的影子，所有的道路上都没有行人的足迹。",
            },
            {
                "original": "孤舟蓑笠翁，独钓寒江雪。",
                "translation": "一条孤单的小船上，一个披蓑衣戴斗笠的老翁，独自在寒冷的江上垂钓。",
            },
        ],
        "meaning": "这首诗描写了冬天江上的寂静，也表现了老渔翁孤独而坚强的品格。",
    },
]


def resolve_poem(
    title: str,
    config: PoemConfig,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    on_result=None,
    max_tokens: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> Optional[dict]:
    """按标题取一首诗的解读。

    顺序：DeepSeek API（有 key 且启用）→ 内置诗库 → None。
    key 来源：优先外部传入（web 设置存的 DB 值），其次环境变量
    ``config.api_key_env``；都没有则只用内置诗库。
    ``model``/``max_tokens``/``timeout_seconds`` 优先外部传入（web 设置的
    ``deepseek_*`` 值），否则用 ``config`` 里的对应值。``on_result`` 是可选
    回调，DeepSeek 每次真实请求后调用（成功或失败），由调用方记入活动记录。
    返回统一结构 dict，或 None（两层都没有，无法解读）。
    """
    normalized = normalize_speech_text(title)
    if not normalized:
        return None
    cleaned = _extract_poem_name(normalized)
    api_key = (api_key or "").strip() or os.environ.get(
        config.api_key_env, ""
    ).strip()
    model = (model or "").strip() or config.model
    if config.enabled and api_key:
        try:
            return interpret_with_deepseek(
                cleaned,
                config,
                api_key=api_key,
                model=model,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                on_result=on_result,
            )
        except (ValueError, URLError, HTTPError, OSError) as exc:
            LOGGER.warning(
                "deepseek interpretation failed; falling back to library: %s",
                exc,
            )
            # 失败回调已在 interpret_with_deepseek 内部触发，这里不再重复。
    return match_poem(cleaned)


# 孩子说诗名时常见的引导语/语气词，去掉后才是干净诗名。
# 注意按长度从长到短排列，避免「帮我讲」先于「帮我讲一下」被剥离。
_POEM_SPOKEN_PREFIXES = (
    "帮我讲一下", "给我讲一下", "讲解一下", "解读一下", "介绍一下",
    "我想听", "我要听", "帮我讲", "给我讲", "讲一下", "讲一讲", "请讲",
)
_POEM_SPOKEN_SUFFIXES = ("谢谢", "多谢", "吧", "呀", "啊", "嗯")


def _extract_poem_name(normalized: str) -> str:
    """从口语归一化文本中提取疑似诗名。"""
    result = normalized
    for prefix in _POEM_SPOKEN_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):]
            break
    for suffix in _POEM_SPOKEN_SUFFIXES:
        if result.endswith(suffix):
            result = result[: -len(suffix)]
            break
    return result


def interpret_with_deepseek(
    title: str,
    config: PoemConfig,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    on_result=None,
    max_tokens: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> dict:
    """请求 DeepSeek 返回结构化的古诗解读；解析失败抛 ValueError。

    ``max_tokens``/``timeout_seconds`` 优先外部传入（web 设置的
    ``deepseek_*``），否则用 ``config`` 默认值。
    """
    api_key = (api_key or "").strip() or os.environ.get(
        config.api_key_env, ""
    ).strip()
    if not api_key:
        raise ValueError("{} is not set".format(config.api_key_env))
    model = (model or "").strip() or config.model
    max_tokens = max_tokens or config.max_tokens
    timeout_seconds = timeout_seconds or config.timeout_seconds
    if _contains_blocked_term(title):
        raise ValueError("poem title contains blocked term")

    system_prompt = (
        "你是一个给小学生讲古诗的老师。只输出 JSON，不要输出任何其他文字。"
        "JSON 结构如下："
        '{"title":"诗名","dynasty":"朝代","author":"作者",'
        '"segments":[{"original":"原句","translation":"白话意思"}],'
        '"meaning":"整首诗的中心意思"}。'
        "segments 按诗句顺序逐句拆分；translation 用适合小学生的口语白话；"
        "meaning 控制在两句话以内。"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请解读古诗《{}》".format(title)},
        ],
        "temperature": 0.4,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    request = Request(
        DEEPSEEK_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(api_key),
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        elapsed = time.monotonic() - started
        content = body["choices"][0]["message"]["content"]
        poem = _parse_poem_json(content)
        poem["_source"] = "deepseek"
        poem["_latency_ms"] = int(elapsed * 1000)
        if _contains_blocked_term(poem.get("meaning", "")):
            raise ValueError("deepseek interpretation blocked")
        if on_result is not None:
            on_result(
                {
                    "status": "success",
                    "poem_title": poem["title"],
                    "model": model,
                    "latency_ms": poem["_latency_ms"],
                    "segments": len(poem["segments"]),
                }
            )
        return poem
    except (URLError, HTTPError, OSError, ValueError) as exc:
        if on_result is not None:
            on_result(
                {
                    "status": "failed",
                    "poem_title": title,
                    "model": model,
                    "error": str(exc),
                }
            )
        raise


def match_poem(normalized: str) -> Optional[dict]:
    """在内置诗库中按标题（含别名）匹配。

    命中唯一返回该诗；未命中返回 None。诗名识别常有语音误差，
    匹配失败返回 None，由调用方引导再说一遍。
    """
    hits = []
    for poem in POEM_LIBRARY:
        names = [poem["title"], *poem.get("aliases", [])]
        if any(
            normalize_speech_text(name) in normalized
            or normalized in normalize_speech_text(name)
            for name in names
        ):
            hits.append(poem)
    if len(hits) == 1:
        return dict(hits[0])
    return None


def _parse_poem_json(content: str) -> dict:
    """DeepSeek 可能返回带 ```json 包裹的内容，容错解析。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    parsed = json.loads(text)
    for key in ("title", "dynasty", "author", "meaning"):
        if key not in parsed or not str(parsed[key]).strip():
            raise ValueError("deepseek response missing {}".format(key))
    segments = parsed.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("deepseek response missing segments")
    clean_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("deepseek segment not a mapping")
        original = str(segment.get("original", "")).strip()
        translation = str(segment.get("translation", "")).strip()
        if not original or not translation:
            raise ValueError("deepseek segment incomplete")
        clean_segments.append(
            {"original": original, "translation": translation}
        )
    return {
        "title": str(parsed["title"]).strip(),
        "dynasty": str(parsed["dynasty"]).strip(),
        "author": str(parsed["author"]).strip(),
        "segments": clean_segments,
        "meaning": str(parsed["meaning"]).strip(),
    }


def _contains_blocked_term(text: str) -> bool:
    normalized = normalize_speech_text(text)
    return any(term in normalized for term in _POEM_BLOCKED_TERMS)
