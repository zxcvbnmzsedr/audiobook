import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from chapter_splitter import split_text_into_chapters
from default_prompts import load_default_prompts
from tagged_script import parse_tagged_script_text


_cancel_flag = {"stop": False}
OPENAI_PROMPT_CACHE_KEY = "audiobook-chapter-pipeline"
OPENAI_PROMPT_CACHE_RETENTION = "24h"
ANNOTATION_CACHE_PROMPT_MARKER = "CACHE_FRIENDLY_ANNOTATION_PROMPT_V1"
CHARACTER_ANALYSIS_CACHE_PROMPT_MARKER = "CACHE_FRIENDLY_CHARACTER_ANALYSIS_PROMPT_V1"
CHAPTER_MEMORY_CACHE_PROMPT_MARKER = "CACHE_FRIENDLY_CHAPTER_MEMORY_PROMPT_V1"
MAX_CHARACTER_TRAITS_CHARS = 320
MAX_VOICE_PROFILE_CHARS = 120
MAX_NARRATOR_STYLE_CHARS = 120
MAX_KEY_TERMS = 120

ANNOTATION_PLACEHOLDER_LABELS = {
    "{context}": "前文连续性上下文",
    "{character_book}": "角色表",
    "{chapter_title}": "章节标题",
    "{chapter_content}": "章节正文",
    "{chunk}": "章节正文",
    "{source_text}": "章节正文",
}

ANNOTATION_CACHE_STABLE_USER_PREAMBLE = f"""
{ANNOTATION_CACHE_PROMPT_MARKER}

本段是跨章节复用的固定指令，用来保持 OpenAI prompt cache 的稳定前缀。不要把下方规则复述到输出中；只按规则生成 tagged 文本。

任务目标：
你要把小说章节转换成有声书 TTS 可直接朗读的标注脚本。小说原文是唯一事实来源。输出必须忠实覆盖原文事件、人物关系、情绪变化、线索、地名、人名、数字、时间、物件和对白。可以为了听感轻微拆句、合并连续旁白、去掉最外层引号、把不适合朗读的视觉符号改写成自然听感，但不能新增剧情、不能总结代替场景、不能把心理活动改成角色说出口、不能改变说话人意图。

输出格式：
每个有效文本段必须独占一行，并以 <说话人:> 开头。旁白、动作、心理、环境、章节标题、结构性文字都用 <旁白:>。只有原文明示说出口的内容才使用角色 canonical 名称。行尾可以追加 {{instruct=...}}，用于描述声音、情绪、节奏、距离感或叙事气质。不要输出 JSON、Markdown、代码块、项目符号、解释、统计信息或任何格式外文本。

说话人规则：
优先使用角色表里的 canonical 名称。遇到别名、称呼、身份称谓时，按角色表合并到 canonical。说话人不确定时使用 <旁白:>，不要激进猜测。连续对白要根据引号、动作归因、上下文轮次判断；若归因不明，宁可保守。旁白永远不要写成 NARRATOR 标签，输出中统一使用 <旁白:>。

对白处理：
对白内容要完整保留。可以去掉外层引号，但不能丢失话语内容。类似“他说”“她问”“陆闻舟低声道”这类归因词可转为旁白，或在不丢信息的前提下转入 instruct。对白附近的动作、停顿、神态、心理反应不能消失；必要时拆成旁白行和角色台词行。多人对话要避免把上一句的情绪、动作或归因错挂到下一位说话人身上。

旁白处理：
旁白要保持作者叙述顺序和信息密度。环境、动作、心理、悬念、线索、物件、空间位置、时间推进都要保留。不要为了短而大幅概括。长段旁白可以拆成数行，但要让听众能连续理解场景。章节标题、卷名、题记等结构文本应作为独立 <旁白:> 行输出。

TTS 友好规则：
单行不要过长，连续旁白可以按语义停顿拆分。非语言声音不要输出方括号舞台提示，例如 [sigh]、[laughs]；应改为可朗读文本，例如 “唉……”、“啊？”、“哈。”、“嗯。”。instruct 要短，描述声音而不是复述动作；例如“压低声音，克制怒意”“稳定叙事，悬疑感”“轻声试探”。旁白缺省可用稳定叙事，但不要每行机械重复完全相同的 instruct。

一致性规则：
使用前文连续性上下文和角色表保持名称、别名、关系、情绪状态、悬而未决线索和叙事风格一致。若前文记忆与当前章节冲突，以当前章节原文为准。角色表只是辅助，不要因为角色表缺失就虚构角色；新出现但说话明确的人名可以按原文输出，后续校验会提示是否补入人物池。

覆盖检查：
输出前在内部检查三件事：第一，原文每个有效段落是否都进入某个标签；第二，所有对白是否保留且说话人没有明显错配；第三，数字、时间、账册、信物、地点、称谓、承诺、威胁、秘密等高权重信息是否没有漏掉。最终只输出 tagged 文本。
""".strip()

ANNOTATION_DYNAMIC_INPUT_TEMPLATE = """
当前章节输入（以下内容每次调用会变化，不属于缓存稳定前缀）：

章节标题：
{chapter_title}

可用角色表 JSON：
{character_book}

前文连续性上下文 JSON：
{context}

当前章节正文：
{chunk}
""".strip()

CHARACTER_ANALYSIS_STABLE_USER_PREAMBLE = f"""
{CHARACTER_ANALYSIS_CACHE_PROMPT_MARKER}

本段是跨章节复用的固定指令，用来保持 prompt cache 的稳定前缀。你是有声书制作系统中的角色分析器，负责维护全局 character_book。只输出 JSON 对象，不要 Markdown，不要解释。

任务目标：
阅读当前章节，在已有 character_book 基础上返回完整更新后的 character_book。角色表用于后续说话人标注、别名归并和音色分配，所以要保守、可追溯、易于 TTS 使用。只记录正文中有依据的人物、组织内具体称呼、重要别名和稳定关系，不要根据类型套路虚构角色，也不要预测后续剧情。

输出结构：
顶层必须包含 characters、narrator_style、genre、key_terms。characters 是数组，每个角色包含 canonical、aliases、traits、voice_profile，可选 confidence。canonical 使用最稳定、最适合脚本标签的名称；aliases 放简称、尊称、身份称呼、误称、旧名等。不要把 NARRATOR、旁白、作者、叙述者加入 characters。

合并规则：
同一人物跨章节出现时必须合并，不要因为称呼变化创建重复角色。已有角色若有新线索，应返回压缩后的稳定档案，而不是把每章状态逐条追加。traits 只写稳定身份、关系、长期剧情功能和核心性格，不写章节流水账；voice_profile 只写可直接用于 TTS 的音色、年龄感、语速、口音、情绪基调或说话习惯。两者不要混杂。

字段标准：
canonical 应短、稳定、适合作为 <角色名:> 标签。若原文既有全名又有简称，优先全名；若只有身份称呼但该人物会持续出现，可以使用最明确的身份称呼。aliases 只放当前 canonical 之外的称呼，不要重复 canonical，不要放空字符串。traits 要回答“这个人是谁、和谁有关、长期承担什么剧情功能、有什么稳定特征”，不超过 240 个汉字。voice_profile 要回答“听起来像什么年龄、性别气质、语速、音色、情绪底色、说话习惯”，不超过 80 个汉字；不要写“站起来”“拿着账册”这类身体动作。

增量更新标准：
已有角色若在本章没有新信息，可以原样保留。已有角色若有新线索，应把旧信息和新信息压缩成一份短档案。不要写“本章未直接出场”“本章主要通过遗物存在”“当前记忆中”等临时状态；章节变化、剧情进展和开放线索应交给 chapter_memory，不要塞进 character_book。若旧 traits 与当前章节冲突，以当前章节为准，但要保留可兼容的信息。若两个角色其实是同一人，要合并 canonical 和 aliases，不要输出两个条目。若一个称呼可能指多人，除非原文能确定，否则不要把它加入某个角色 aliases。

人物筛选标准：
有稳定姓名、明确对白、关键行动、证据关联、亲属/上下级关系、嫌疑/被害/见证身份的人物应记录。纯群体如“众人”“衙役们”“百姓”通常不记录。只出现一次、没有对白且不影响后文的人，可以不记录。官职称呼若明确指向某个稳定个体，可以记录为 aliases；若只是泛称，不要新增角色。

音色标准：
voice_profile 要服务人工配置 TTS 音色时的筛选和判断。可以写“青年男声，语速偏快，常带讥诮”“中年官员，低沉克制，审问时压迫感强”“少女声线，紧张时尾音发颤”。不要写过长文学描写，不要塞剧情复述，不要混入外貌细节，除非外貌直接影响听感或身份辨认。

key_terms 标准：
key_terms 放短词，不放长句。适合记录案卷名、账册、赦书、地名、组织名、制度名、关键物件、暗号、特殊称谓。不要放普通动词、泛泛情绪词或完整剧情摘要。已有 key_terms 要保留，新增项要去重。

返回完整性：
每次都返回完整 character_book，而不是只返回新增人物。不要省略已有 characters。不要把 commentary、analysis、reasoning、修订说明放进 JSON。所有字符串必须可被 JSON 解析，不要尾随逗号，不要用中文引号替代 JSON 双引号。

保守规则：
只在有原文证据时新增角色。路人、群体、官职、泛称若没有稳定个体身份，通常不要新增为角色。对未知人物可暂时忽略，除非其对白或剧情功能明确需要后续追踪。key_terms 放专名、物件、地点、制度、案件线索等对后续连续性有帮助的短词。

质量检查：
输出前在内部检查：没有旁白角色；没有明显重复 canonical；aliases 没有空字符串；traits 和 voice_profile 是短而有用的中文说明；JSON 可解析；返回的是完整 character_book 而不是增量 patch。
""".strip()

CHAPTER_MEMORY_STABLE_USER_PREAMBLE = f"""
{CHAPTER_MEMORY_CACHE_PROMPT_MARKER}

本段是跨章节复用的固定指令，用来保持 prompt cache 的稳定前缀。你是有声书制作系统的连续性记录员。只输出 JSON 对象，不要 Markdown，不要解释。

任务目标：
为当前章节生成轻量 chapter_memory，供后续章节保持人物、关系、情绪、案件线索、场景状态和叙事语气连续。记忆必须来自当前章节原文和本章脚本条目；可以结合前文记忆理解状态变化，但不能推测后续剧情，不能写未发生的结论。

输出结构：
必须包含 summary、ending_state、character_updates、relationship_updates、tone_notes、open_threads。summary 用 1-3 句概括本章发生的关键事件。ending_state 记录本章结束时人物、证据、危险、地点或心理状态。character_updates 是人物状态、动机、秘密、伤势、身份变化等短句数组。relationship_updates 是人物之间信任、冲突、债务、威胁、误解、承诺等变化。tone_notes 是对后续朗读有帮助的气氛、节奏、情绪基调。open_threads 是尚未解决的线索、问题、悬念或行动目标。

压缩规则：
chapter_memory 是给后续 prompt 使用的，不是剧情复述全文。每条信息要短、具体、可复用。不要写空泛评价，不要加入作者分析，不要把脚本格式说明写进去。人物名要尽量使用角色表 canonical，物件和地名保持原文名称。若某个数组没有新信息，返回空数组。

字段写法标准：
summary 只写本章核心事件，不要超过三句，不要引入下一章猜测。ending_state 写“本章结束时”的状态，例如谁掌握了什么、谁被怀疑、证据在哪里、危险是否升级、角色心理是否转变。character_updates 每条只写一个人物或一组紧密相关人物的新状态。relationship_updates 只写关系变化或互动压力，例如信任增加、互相试探、债务形成、威胁升级、误会加深。tone_notes 给后续演播使用，写悬疑、压抑、轻讽、急促、悲伤、审问感、雨夜冷感等可朗读的气氛。open_threads 写还没解决的问题，不要写已经解决的事件。

抽取优先级：
优先记录会影响后续章节理解的信息：证据、秘密、承诺、威胁、伤势、身份暴露、人物立场变化、关系裂痕、地点变化、物件去向、官司/案件推进、时间限制、未兑现行动。其次记录有助于保持朗读风格的气氛和情绪。不要记录纯粹重复的旁白修辞，除非它成为关键意象或线索。

压缩粒度：
每条数组项应该是一句短句。不要把整段剧情塞进数组。不要把多个无关事实合成一条。不要使用“可能”“似乎预示”“应该会”等未来推测词。可以写“沈照微开始怀疑账页缺口与陆闻舟有关”，不能写“沈照微以后会发现陆闻舟被陷害”。可以写“曹里正和仓丁口供过于一致，显得像被预先教过”，不能写“幕后黑手就是马书办”，除非当前章节明确说出。

名称一致性：
角色名优先使用角色表 canonical。若脚本条目里出现别名，要尽量映射回 canonical。物件、地点、书信、账册、官职等保持原文名。不要把同一个人一会儿写全名、一会儿写称谓，除非原文关系本身需要记录这个称谓变化。

返回完整性：
只返回 JSON 对象。不要返回数组作为顶层。不要添加“以下是 JSON”之类说明。所有字段必须存在；数组字段即使为空也返回 []。字符串要短而明确。JSON 里不要包含 Markdown 列表符号、代码块、注释或尾随逗号。

错误示例规避：
不要把整章脚本重新复制到 summary。不要把“角色说了很多话”“气氛紧张”当作有效记忆，必须指出谁、什么证据、什么关系或什么悬念。不要写“主角继续调查”这种泛句，除非同时说明调查对象和当前进展。不要记录已经从前文记忆中原样出现且本章没有变化的事实。不要把读者视角分析、主题解读、修辞赏析写入 memory。不要把 open_threads 写成确定答案；open_threads 应保持问题状态，例如“账页缺失旁证签的原因未明”“陆闻舟与副页末尾署名的关系仍待查”。

质量检查：
输出前在内部检查：JSON 可解析；所有字段存在；没有 Markdown；没有未来推测；没有与当前章节冲突的信息；summary 和 ending_state 不为空，除非章节本身几乎没有正文。
""".strip()


def _handle_sigterm(signum, frame):
    _cancel_flag["stop"] = True


def emit(event_type: str, **data: Any) -> None:
    print(f"[EVENT] {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}", flush=True)


def emit_llm_attempt(label: str, attempt: int, stage: str, expected: str = "") -> None:
    payload: dict[str, Any] = {
        "label": label,
        "attempt": attempt,
        "stage": stage,
    }
    if expected:
        payload["expected"] = expected
    emit("llm_attempt", **payload)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: failed to read {path}: {exc}", flush=True)
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def chapter_memory_path(workspace_dir: Path) -> Path:
    return workspace_dir / "chapter_memory.json"


def script_issues_path(workspace_dir: Path) -> Path:
    return workspace_dir / "script_issues.json"


def state_path(workspace_dir: Path) -> Path:
    return workspace_dir / "script_generation_state.json"


def character_analysis_state_path(workspace_dir: Path) -> Path:
    return workspace_dir / "character_analysis_state.json"


def init_generation_state(
    workspace_dir: Path,
    chapters: list[dict[str, Any]],
    selected_chapter_ids: set[str],
    model_name: str,
    provider: str,
    mode: str,
    reuse_character_book: bool,
    enable_chapter_memory: bool,
) -> dict[str, Any]:
    path = state_path(workspace_dir)
    state = read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    chapter_state = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        if not chapter_id:
            continue
        chapter_state[chapter_id] = {
            **(chapter_state.get(chapter_id) or {}),
            "chapter_id": chapter_id,
            "chapter_index": chapter.get("index"),
            "chapter_title": chapter.get("title") or chapter_id,
            "status": "pending",
            "entry_count": 0,
            "parse_issues": 0,
            "error": "",
            "selected": True,
            "updated_at": now_iso(),
        }
    state = {
        **state,
        "engine": "character_pipeline" if mode == "characters" else "chapter_pipeline",
        "mode": mode,
        "reuse_character_book": reuse_character_book,
        "enable_chapter_memory": enable_chapter_memory,
        "model": model_name,
        "provider": provider,
        "selected_chapter_ids": sorted(selected_chapter_ids),
        "started_at": now_iso(),
        "finished_at": "",
        "status": "running",
        "chapters": chapter_state,
    }
    write_json(path, state)
    return state


def update_chapter_state(workspace_dir: Path, chapter_id: str, **fields: Any) -> None:
    path = state_path(workspace_dir)
    state = read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    current = chapters.get(chapter_id) if isinstance(chapters.get(chapter_id), dict) else {}
    chapters[chapter_id] = {**current, "chapter_id": chapter_id, **fields, "updated_at": now_iso()}
    state["chapters"] = chapters
    write_json(path, state)


def finish_generation_state(workspace_dir: Path, status: str, **fields: Any) -> None:
    path = state_path(workspace_dir)
    state = read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    state.update(fields)
    state["status"] = status
    state["finished_at"] = now_iso()
    write_json(path, state)


def load_character_analysis_state(workspace_dir: Path) -> dict[str, Any]:
    state = read_json(character_analysis_state_path(workspace_dir), {})
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("chapters"), dict):
        state["chapters"] = {}
    return state


def update_character_analysis_state(
    workspace_dir: Path,
    chapter: dict[str, Any],
    *,
    status: str,
    characters: int,
    error: str = "",
) -> None:
    path = character_analysis_state_path(workspace_dir)
    state = load_character_analysis_state(workspace_dir)
    chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}
    chapter_id = str(chapter.get("chapter_id") or "")
    if not chapter_id:
        return
    item = chapters.get(chapter_id) if isinstance(chapters.get(chapter_id), dict) else {}
    now = now_iso()
    chapters[chapter_id] = {
        **item,
        "chapter_id": chapter_id,
        "chapter_index": chapter.get("index"),
        "chapter_title": chapter.get("title") or chapter_id,
        "char_count": len(str(chapter.get("content") or "")),
        "status": status,
        "characters": characters,
        "error": error,
        "updated_at": now,
    }
    state["chapters"] = chapters
    state["status"] = status
    state["updated_at"] = now
    write_json(path, state)


def load_chapter_memory(workspace_dir: Path) -> dict[str, Any]:
    memory = read_json(chapter_memory_path(workspace_dir), {})
    if not isinstance(memory, dict):
        memory = {}
    chapters = memory.get("chapters")
    if not isinstance(chapters, dict):
        memory["chapters"] = {}
    return memory


def save_chapter_memory(workspace_dir: Path, memory: dict[str, Any]) -> None:
    memory["updated_at"] = now_iso()
    memory.setdefault("chapters", {})
    write_json(chapter_memory_path(workspace_dir), memory)


def load_script_issues(workspace_dir: Path) -> dict[str, Any]:
    issues = read_json(script_issues_path(workspace_dir), {})
    if not isinstance(issues, dict):
        issues = {}
    chapters = issues.get("chapters")
    if not isinstance(chapters, dict):
        issues["chapters"] = {}
    return issues


def save_script_issues(workspace_dir: Path, issues: dict[str, Any]) -> None:
    issues["updated_at"] = now_iso()
    issues.setdefault("chapters", {})
    write_json(script_issues_path(workspace_dir), issues)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def resolve_provider(config: dict[str, Any]) -> str:
    llm = config.get("llm") or {}
    generation = config.get("generation") or {}
    provider = str(llm.get("provider") or generation.get("agent_provider") or "anthropic").strip().lower()
    if provider in {"claude", "anthropic"}:
        return "anthropic"
    if provider in {"openai", "openai_chat", "openai_responses"}:
        return "openai"
    return provider or "anthropic"


def resolve_model_name(config: dict[str, Any]) -> str:
    generation = config.get("generation") or {}
    llm = config.get("llm") or {}
    return str(generation.get("model_name") or generation.get("agent_model") or llm.get("model_name") or "claude-opus-4-7")


def resolve_openai_api_type(config: dict[str, Any]) -> str:
    llm = config.get("llm") or {}
    value = str(llm.get("openai_api_type") or "responses").strip().lower()
    if value in {"chat", "chat_completions", "chat-completions"}:
        return "chat"
    return "responses"


def resolve_api_key(config: dict[str, Any], provider: str) -> str:
    llm = config.get("llm") or {}
    configured = str(llm.get("api_key") or "").strip()
    if configured:
        return configured
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY") or ""
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY") or ""
    return ""


def resolve_base_url(config: dict[str, Any]) -> str:
    llm = config.get("llm") or {}
    return str(llm.get("base_url") or "").strip()


def build_chat_model(config: dict[str, Any]) -> BaseChatModel:
    provider = resolve_provider(config)
    model_name = resolve_model_name(config)
    api_key = resolve_api_key(config, provider)
    base_url = resolve_base_url(config)
    generation = config.get("generation") or {}
    temperature = float(generation.get("temperature", 0.3))
    max_tokens = int(generation.get("max_tokens") or 4096)

    if provider == "openai":
        if not api_key:
            raise RuntimeError("llm.api_key or OPENAI_API_KEY is required for OpenAI chapter generation")
        os.environ["OPENAI_API_KEY"] = api_key
        model_kwargs = {
            "prompt_cache_key": OPENAI_PROMPT_CACHE_KEY,
            "prompt_cache_retention": OPENAI_PROMPT_CACHE_RETENTION,
        }
        kwargs: dict[str, Any] = {
            "model": model_name,
            "api_key": api_key,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "model_kwargs": model_kwargs,
        }
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["use_responses_api"] = resolve_openai_api_type(config) != "chat"
        print(
            "[LLM] OpenAI prompt cache enabled "
            f"key={OPENAI_PROMPT_CACHE_KEY} retention={OPENAI_PROMPT_CACHE_RETENTION}",
            flush=True,
        )
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        if not api_key:
            raise RuntimeError("llm.api_key or ANTHROPIC_API_KEY is required for Anthropic chapter generation")
        os.environ["ANTHROPIC_API_KEY"] = api_key

    kwargs = {
        "model_provider": provider,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return init_chat_model(model_name, **kwargs)


def message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content or "")


def emit_llm_stream(label: str, text: str = "", *, done: bool = False, chars: int = 0, elapsed_ms: int | None = None) -> None:
    payload: dict[str, Any] = {"label": label}
    if text:
        payload["text"] = text
    if done:
        payload["done"] = True
        payload["chars"] = chars
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
    emit("llm_stream", **payload)


def invoke_streaming_message(model: BaseChatModel, messages: list[Any], label: str) -> str:
    """Stream an LLM response for live logs, while returning the complete text."""
    started_at = time.perf_counter()
    if not hasattr(model, "stream"):
        message = model.invoke(messages)
        log_usage_metadata(label, message)
        text = message_to_text(message)
        emit_llm_stream(label, done=True, chars=len(text), elapsed_ms=int((time.perf_counter() - started_at) * 1000))
        return text

    parts: list[str] = []
    pending: list[str] = []
    pending_chars = 0
    last_usage_chunk: Any = None

    def flush_pending(force: bool = False) -> None:
        nonlocal pending, pending_chars
        if not pending:
            return
        text = "".join(pending)
        if not force and len(text) < 240 and "\n" not in text:
            return
        emit_llm_stream(label, text)
        pending = []
        pending_chars = 0

    try:
        for chunk in model.stream(messages):
            usage = getattr(chunk, "usage_metadata", None)
            if isinstance(usage, dict):
                last_usage_chunk = chunk
            delta = message_to_text(chunk)
            if not delta:
                continue
            parts.append(delta)
            pending.append(delta)
            pending_chars += len(delta)
            if pending_chars >= 240 or "\n" in delta:
                flush_pending(force=True)
        flush_pending(force=True)
        text = "".join(parts)
        emit_llm_stream(label, done=True, chars=len(text), elapsed_ms=int((time.perf_counter() - started_at) * 1000))
        if last_usage_chunk is not None:
            log_usage_metadata(label, last_usage_chunk)
        return text
    except Exception as exc:
        if parts:
            raise
        print(f"[LLM_STREAM] streaming unavailable for {label}, falling back to invoke: {exc}", flush=True)
        message = model.invoke(messages)
        log_usage_metadata(label, message)
        text = message_to_text(message)
        emit_llm_stream(label, done=True, chars=len(text), elapsed_ms=int((time.perf_counter() - started_at) * 1000))
        return text


def extract_json(text: str, expected: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    decoder = json.JSONDecoder()
    start_chars = ["{"] if expected == "object" else ["["]
    for idx, ch in enumerate(cleaned):
        if ch not in start_chars:
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[idx:])
            if expected == "object" and isinstance(value, dict):
                return value
            if expected == "array" and isinstance(value, list):
                return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"LLM response did not contain a JSON {expected}: {cleaned[:300]}")


def _usage_value(details: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in details:
            return details.get(key)
    return None


def log_usage_metadata(label: str, message: Any) -> None:
    usage = getattr(message, "usage_metadata", None)
    if usage is None and isinstance(message, dict):
        usage = message.get("usage_metadata")
    if not isinstance(usage, dict):
        return

    input_details = usage.get("input_token_details") or {}
    cache_read = _usage_value(
        input_details,
        "cache_read",
        "cached_tokens",
        "priority_cache_read",
        "flex_cache_read",
    )
    print(
        "[LLM] usage "
        f"label={label} input_tokens={usage.get('input_tokens')} "
        f"output_tokens={usage.get('output_tokens')} total_tokens={usage.get('total_tokens')} "
        f"cache_read={cache_read}",
        flush=True,
    )
    emit(
        "llm_usage",
        label=label,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        cache_read=cache_read,
    )


def invoke_json(model: BaseChatModel, system_prompt: str, user_prompt: str, expected: str, stage: str = "json") -> Any:
    last_error = ""
    prompt = user_prompt
    for attempt in range(1, 4):
        label = f"json_{expected}_attempt_{attempt}"
        emit_llm_attempt(label, attempt, stage, expected)
        text = invoke_streaming_message(model, [SystemMessage(content=system_prompt), HumanMessage(content=prompt)], label)
        try:
            return extract_json(text, expected)
        except Exception as exc:
            last_error = str(exc)
            prompt = (
                f"{user_prompt}\n\n"
                "上一次响应不是可解析的目标 JSON。"
                f"错误：{last_error}\n"
                "请重新输出，且只输出 JSON，不要 Markdown，不要解释。"
            )
            emit("llm_retry", label=label, attempt=attempt, stage=stage, error=last_error)
            print(f"Warning: JSON parse failed on attempt {attempt}: {last_error}", flush=True)
    raise ValueError(last_error or "LLM did not return parseable JSON")


def invoke_text(model: BaseChatModel, system_prompt: str, user_prompt: str) -> str:
    text = invoke_streaming_message(model, [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)], "text")
    return text.strip()


def sanitize_tagged_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:text|txt)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


TAGGED_LABEL_RE = re.compile(r"<\s*([^:：<>]+?)\s*[:：]\s*>")


def reject_non_tagged_annotation(text: str) -> list[dict[str, Any]]:
    stripped = str(text or "").strip()
    if not stripped:
        return [{"level": "warning", "message": "LLM 响应为空。"}]

    if TAGGED_LABEL_RE.search(stripped):
        return []

    issues = [{
        "level": "error",
        "message": "LLM 响应没有任何 <角色:> tagged 标签。",
    }]
    if stripped[0] in "[{":
        try:
            json.loads(stripped)
            issues.append({
                "level": "error",
                "message": "LLM 返回了 JSON，但章节标注管线要求直接返回 tagged 文本。",
            })
        except json.JSONDecodeError:
            pass
    return issues


def invoke_tagged_entries(
    model: BaseChatModel,
    system_prompt: str,
    user_prompt: str,
    chapter_meta: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], str]:
    prompt = user_prompt
    last_issues: list[dict[str, Any]] = []
    last_text = ""
    for attempt in range(1, 4):
        label = f"tagged_attempt_{attempt}"
        emit_llm_attempt(label, attempt, "tagged", "tagged_text")
        tagged_text = sanitize_tagged_text(invoke_streaming_message(
            model,
            [SystemMessage(content=system_prompt), HumanMessage(content=prompt)],
            label,
        ))
        issues = reject_non_tagged_annotation(tagged_text)
        entries: list[dict[str, Any]] = []
        if not issues:
            entries, issues = parse_tagged_script_text(tagged_text, chapter_meta=chapter_meta)
        if entries:
            return entries, issues, tagged_text
        last_issues = issues
        last_text = tagged_text
        prompt = (
            f"{user_prompt}\n\n"
            "上一次响应无法解析出任何有效标注条目。"
            f"解析问题：{json.dumps(issues, ensure_ascii=False)}\n"
            f"上一次响应片段：{tagged_text[:800]}\n\n"
            "请重新输出，且只输出 tagged 文本。每段必须以 <旁白:> 或 <角色名:> 开头，"
            "不要 Markdown，不要 JSON，不要解释。"
        )
        emit("llm_retry", label=label, attempt=attempt, stage="tagged", issues=issues)
        print(f"Warning: tagged script parse failed on attempt {attempt}: {issues}", flush=True)
    raise ValueError(
        "LLM response did not contain parseable tagged script entries: "
        f"{json.dumps(last_issues, ensure_ascii=False)}; text={last_text[:300]}"
    )


def default_character_book() -> dict[str, Any]:
    return {
        "characters": [],
        "narrator_style": "清晰、稳定、叙事感强",
        "genre": "",
        "key_terms": [],
    }


def character_profile_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(part).strip() for part in value if str(part).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "").strip()


def _split_profile_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", "", str(text or "").strip())
    if not text:
        return []
    parts = re.split(r"[；;。！？!?]\s*", text)
    return [part.strip(" ，、,") for part in parts if part.strip(" ，、,")]


def _profile_sentence_is_temporary(sentence: str, *, kind: str) -> bool:
    if not sentence:
        return True
    temporary_markers = (
        "本章未直接出场",
        "本章主要通过",
        "当前记忆中",
        "当前章节",
        "五年后本章",
    )
    if any(marker in sentence for marker in temporary_markers):
        return True
    if sentence.startswith(("本章", "此前", "当前")):
        return True
    return False


def _profile_sentence_score(sentence: str, *, kind: str) -> int:
    if kind == "voice_profile":
        keywords = (
            "声线", "男声", "女声", "童声", "老年", "青年", "中年", "少年",
            "少女", "低沉", "清亮", "沙哑", "语速", "口音", "克制", "温和",
            "冷硬", "急促", "缓慢", "尾音", "情绪", "压抑", "疲惫",
        )
    else:
        keywords = (
            "之女", "之子", "母亲", "父亲", "县令", "书办", "书吏", "差役",
            "官员", "家人", "亲属", "熟悉", "负责", "主导", "参与", "见证",
            "证人", "被害", "被卷入", "为", "与", "性格", "克制", "谨慎",
        )
    score = sum(2 for keyword in keywords if keyword in sentence)
    score += max(0, 4 - len(sentence) // 80)
    if kind == "traits" and any(marker in sentence for marker in ("本章", "此前", "未直接出场")):
        score -= 5
    return score


def compact_profile_text(existing: Any, incoming: Any = "", *, max_chars: int, kind: str) -> str:
    sentences: list[str] = []
    seen = set()
    for source in (existing, incoming):
        for sentence in _split_profile_sentences(character_profile_text(source)):
            if _profile_sentence_is_temporary(sentence, kind=kind):
                continue
            key = sentence.casefold()
            if key in seen:
                continue
            seen.add(key)
            sentences.append(sentence)
    if not sentences:
        return ""

    indexed = list(enumerate(sentences))
    indexed.sort(key=lambda item: (-_profile_sentence_score(item[1], kind=kind), item[0]))
    chosen: list[tuple[int, str]] = []
    used = 0
    for original_index, sentence in indexed:
        extra = len(sentence) + (1 if chosen else 0)
        if used + extra <= max_chars:
            chosen.append((original_index, sentence))
            used += extra
    if not chosen:
        first = indexed[0][1]
        return first[:max_chars].rstrip("，、；。,. ")
    chosen.sort(key=lambda item: item[0])
    return "；".join(sentence for _, sentence in chosen).strip()


def compact_key_terms(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    terms: list[str] = []
    seen = set()
    for term in value:
        text = str(term or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(text)
        if len(terms) >= MAX_KEY_TERMS:
            break
    return terms


def normalize_character_book(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return default_character_book()

    normalized = default_character_book()
    narrator_style = compact_profile_text(
        value.get("narrator_style") or normalized["narrator_style"],
        "",
        max_chars=MAX_NARRATOR_STYLE_CHARS,
        kind="voice_profile",
    )
    normalized["narrator_style"] = narrator_style or normalized["narrator_style"]
    normalized["genre"] = str(value.get("genre") or "")
    normalized["key_terms"] = compact_key_terms(value.get("key_terms") or [])

    seen = set()
    for item in value.get("characters") or []:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or item.get("name") or "").strip()
        if not canonical or canonical.upper() == "NARRATOR":
            continue
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        aliases = sorted({str(alias).strip() for alias in aliases if str(alias).strip()})
        traits = character_profile_text(
            item.get("traits")
            or item.get("description")
            or item.get("描述")
            or item.get("人设")
            or item.get("身份")
            or item.get("性格")
            or ""
        )
        voice_profile = character_profile_text(
            item.get("voice_profile")
            or item.get("voice_style")
            or item.get("voice_description")
            or item.get("声音倾向")
            or item.get("音色")
            or item.get("音色描述")
            or item.get("声音描述")
            or item.get("style")
            or ""
        )
        traits = compact_profile_text(
            traits,
            "",
            max_chars=MAX_CHARACTER_TRAITS_CHARS,
            kind="traits",
        )
        voice_profile = compact_profile_text(
            voice_profile,
            "",
            max_chars=MAX_VOICE_PROFILE_CHARS,
            kind="voice_profile",
        )
        if not traits and not voice_profile and not aliases:
            continue
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        character = {
            "canonical": canonical,
            "aliases": aliases,
            "traits": traits,
            "voice_profile": voice_profile,
        }
        try:
            character["confidence"] = max(0.0, min(1.0, float(item.get("confidence", 0.8))))
        except (TypeError, ValueError):
            character["confidence"] = 0.8
        normalized["characters"].append(character)
    return normalized


def merge_character_books(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = normalize_character_book(base)
    incoming = normalize_character_book(incoming)
    if incoming.get("narrator_style"):
        merged["narrator_style"] = compact_profile_text(
            merged.get("narrator_style"),
            incoming.get("narrator_style"),
            max_chars=MAX_NARRATOR_STYLE_CHARS,
            kind="voice_profile",
        )
    if incoming.get("genre"):
        merged["genre"] = incoming["genre"]
    merged["key_terms"] = compact_key_terms([*(merged.get("key_terms") or []), *(incoming.get("key_terms") or [])])

    def match_index(character: dict[str, Any]) -> int | None:
        names = {character["canonical"].casefold()}
        names.update(alias.casefold() for alias in character.get("aliases") or [])
        for idx, existing in enumerate(merged["characters"]):
            existing_names = {existing["canonical"].casefold()}
            existing_names.update(alias.casefold() for alias in existing.get("aliases") or [])
            if names & existing_names:
                return idx
        return None

    for character in incoming["characters"]:
        idx = match_index(character)
        if idx is None:
            merged["characters"].append(character)
            continue
        existing = merged["characters"][idx]
        existing["aliases"] = sorted(set(existing.get("aliases") or []) | set(character.get("aliases") or []))
        existing["traits"] = compact_profile_text(
            existing.get("traits"),
            character.get("traits"),
            max_chars=MAX_CHARACTER_TRAITS_CHARS,
            kind="traits",
        )
        existing["voice_profile"] = compact_profile_text(
            existing.get("voice_profile"),
            character.get("voice_profile"),
            max_chars=MAX_VOICE_PROFILE_CHARS,
            kind="voice_profile",
        )
        existing["confidence"] = max(float(existing.get("confidence", 0.0)), float(character.get("confidence", 0.0)))
    return merged


def character_voice_style(character: dict[str, Any]) -> str:
    voice_profile = character_profile_text(character.get("voice_profile"))
    if voice_profile:
        return voice_profile
    aliases = character.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = [aliases]
    parts = []
    alias_text = "、".join(str(alias).strip() for alias in aliases if str(alias).strip())
    if alias_text:
        parts.append(f"别名：{alias_text}")
    traits_text = character_profile_text(character.get("traits"))
    if traits_text:
        parts.append(traits_text)
    return "；".join(parts).strip()


def character_lookup(character_book: dict[str, Any]) -> dict[str, str]:
    lookup = {"narrator": "NARRATOR", "旁白": "NARRATOR"}
    for character in character_book.get("characters") or []:
        canonical = str(character.get("canonical") or "").strip()
        if not canonical:
            continue
        lookup[canonical.casefold()] = canonical
        for alias in character.get("aliases") or []:
            alias = str(alias or "").strip()
            if alias:
                lookup[alias.casefold()] = canonical
    return lookup


def normalize_script_entries(entries: list[dict[str, Any]], character_book: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    lookup = character_lookup(character_book)
    updates = 0
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        speaker = str(item.get("speaker") or item.get("type") or "").strip()
        canonical = lookup.get(speaker.casefold(), speaker)
        if canonical != speaker:
            updates += 1
        item["speaker"] = canonical or "NARRATOR"
        item.pop("type", None)
        normalized.append(item)
    return normalized, updates


def recent_context_for_chapter(
    memory: dict[str, Any],
    all_chapters: list[dict[str, Any]],
    chapter_id: str,
    existing_entries: Any,
    limit: int = 3,
) -> dict[str, Any]:
    chapters_memory = memory.get("chapters") if isinstance(memory.get("chapters"), dict) else {}
    chapter_ids = [str(chapter.get("chapter_id") or "") for chapter in all_chapters]
    try:
        current_index = chapter_ids.index(chapter_id)
    except ValueError:
        current_index = len(chapter_ids)
    previous_ids = [cid for cid in chapter_ids[max(0, current_index - limit):current_index] if cid]
    previous_memory = [
        chapters_memory[cid]
        for cid in previous_ids
        if isinstance(chapters_memory.get(cid), dict)
    ]

    recent_entries = []
    if isinstance(existing_entries, list):
        previous_set = set(previous_ids)
        for entry in reversed(existing_entries):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("chapter_id") or "") not in previous_set:
                continue
            recent_entries.append({
                "chapter_id": entry.get("chapter_id"),
                "speaker": entry.get("speaker") or entry.get("type") or "",
                "text": str(entry.get("text") or "")[:180],
                "instruct": entry.get("instruct") or "",
            })
            if len(recent_entries) >= 6:
                break
    recent_entries.reverse()
    return {
        "previous_chapter_memory": previous_memory,
        "recent_script_entries": recent_entries,
    }


def normalize_memory_payload(value: Any, chapter: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    chapter_id = str(chapter.get("chapter_id") or "")
    title = str(chapter.get("title") or chapter_id)
    speakers = sorted({
        str(entry.get("speaker") or "")
        for entry in entries
        if str(entry.get("speaker") or "").strip() and str(entry.get("speaker") or "").upper() != "NARRATOR"
    })
    return {
        "chapter_id": chapter_id,
        "chapter_index": chapter.get("index"),
        "chapter_title": title,
        "summary": str(value.get("summary") or "").strip(),
        "ending_state": str(value.get("ending_state") or "").strip(),
        "character_updates": value.get("character_updates") if isinstance(value.get("character_updates"), list) else [],
        "relationship_updates": value.get("relationship_updates") if isinstance(value.get("relationship_updates"), list) else [],
        "tone_notes": value.get("tone_notes") if isinstance(value.get("tone_notes"), list) else [],
        "open_threads": value.get("open_threads") if isinstance(value.get("open_threads"), list) else [],
        "speakers": speakers,
        "entry_count": len(entries),
        "updated_at": now_iso(),
        "stale": False,
    }


def compact_for_coverage(value: Any) -> str:
    text = str(value or "").casefold()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


SOURCE_COVERAGE_CATEGORY_META = {
    "dialogue": {"label": "对白", "weight": 3.0, "severity": "high"},
    "key_detail": {"label": "关键线索", "weight": 2.5, "severity": "high"},
    "number_time": {"label": "数字/时间", "weight": 2.2, "severity": "high"},
    "state_relation": {"label": "人物状态", "weight": 1.8, "severity": "medium"},
    "narration": {"label": "叙述", "weight": 1.0, "severity": "low"},
}

SOURCE_DIALOGUE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,160})[”\"」』]")
SOURCE_NUMBER_TIME_RE = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿\d０-９]{1,8}"
    r"(?:年|月|日|天|夜|更|刻|时|里|丈|尺|寸|枚|封|页|两|钱|人|次|遍|章|号|件|条|本|把|只|盏|间|处|座|个)?"
)
SOURCE_KEY_DETAIL_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{1,10}"
    r"(?:符|令|信|书|册|账|页|刀|剑|门|楼|城|巷|山|寺|院|房|厅|县|府|印|封|纸|卷|案|灯|钟|铜|银|金|药|血|骨|名册|铜符)"
)
SOURCE_STATE_RE = re.compile(
    r"(?:记起|想起|知道|明白|意识到|相信|怀疑|害怕|愤怒|迟疑|沉默|攥|盯|望|等|逼|必须|不能|没有说完|关系|承诺|背叛|威胁|秘密|密语)"
)


def source_coverage_display(value: str, *, limit: int = 96) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def source_coverage_point_key(category: str, text: str) -> str:
    return f"{category}:{compact_for_coverage(text)[:80]}"


def source_coverage_add_point(points: list[dict[str, Any]], seen: set[str], category: str, text: str) -> None:
    compact = compact_for_coverage(text)
    if len(compact) < 3:
        return
    key = source_coverage_point_key(category, text)
    if key in seen:
        return
    seen.add(key)
    meta = SOURCE_COVERAGE_CATEGORY_META.get(category, SOURCE_COVERAGE_CATEGORY_META["narration"])
    points.append({
        "category": category,
        "category_label": meta["label"],
        "severity": meta["severity"],
        "weight": meta["weight"],
        "text": source_coverage_display(text),
        "compact": compact,
    })


def source_coverage_points(source_text: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_text = str(source_text or "")

    for match in SOURCE_DIALOGUE_RE.finditer(source_text):
        source_coverage_add_point(points, seen, "dialogue", match.group(1))

    for match in SOURCE_NUMBER_TIME_RE.finditer(source_text):
        token = match.group(0)
        if len(compact_for_coverage(token)) >= 2:
            source_coverage_add_point(points, seen, "number_time", token)

    for match in SOURCE_KEY_DETAIL_RE.finditer(source_text):
        token = match.group(0)
        if len(compact_for_coverage(token)) >= 3:
            source_coverage_add_point(points, seen, "key_detail", token)

    raw_units = [
        part.strip()
        for part in re.split(r"[\n。！？!?；;]+", source_text)
        if part.strip()
    ]
    for raw in raw_units:
        compact = compact_for_coverage(raw)
        if len(compact) < 6:
            continue
        category = "state_relation" if SOURCE_STATE_RE.search(raw) else "narration"
        if len(compact) > 80:
            for idx in range(0, len(compact), 60):
                unit = compact[idx:idx + 80]
                if len(unit) >= 6:
                    source_coverage_add_point(points, seen, category, unit)
        else:
            source_coverage_add_point(points, seen, category, raw)

    return points


def source_point_covered(point: dict[str, Any], generated_compact: str) -> bool:
    compact = str(point.get("compact") or "")
    if not compact:
        return True
    if compact in generated_compact:
        return True
    category = point.get("category")
    if category in {"number_time", "key_detail"}:
        return len(compact) >= 2 and compact in generated_compact

    window = 10 if category == "dialogue" else 16
    if len(compact) <= window:
        return compact in generated_compact

    hits = 0
    probes = 0
    step = max(6, window // 2)
    for start in range(0, max(1, len(compact) - window + 1), step):
        probe = compact[start:start + window]
        if len(probe) < min(8, window):
            continue
        probes += 1
        if probe in generated_compact:
            hits += 1
    if probes == 0:
        return False
    required_ratio = 0.34 if category == "narration" else 0.5
    return (hits / probes) >= required_ratio


def source_coverage_summary(points: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for category, meta in SOURCE_COVERAGE_CATEGORY_META.items():
        category_points = [point for point in points if point.get("category") == category]
        total_weight = sum(float(point.get("weight") or 0) for point in category_points)
        covered_weight = sum(float(point.get("weight") or 0) for point in category_points if point.get("covered"))
        summary[category] = {
            "label": meta["label"],
            "severity": meta["severity"],
            "total": len(category_points),
            "covered": sum(1 for point in category_points if point.get("covered")),
            "missing": sum(1 for point in category_points if not point.get("covered")),
            "weight": round(total_weight, 2),
            "covered_weight": round(covered_weight, 2),
            "ratio": round(covered_weight / total_weight, 4) if total_weight else 1.0,
        }
    return summary


def source_coverage_report(source_text: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    source_text = str(source_text or "")
    generated_text = "\n".join(str(entry.get("text") or "") for entry in entries if isinstance(entry, dict))
    source_compact = compact_for_coverage(source_text)
    generated_compact = compact_for_coverage(generated_text)
    if not source_compact:
        return {
            "source_char_count": 0,
            "generated_text_char_count": len(generated_text),
            "source_coverage_ratio": 1.0,
            "source_coverage_text_ratio": 1.0,
            "source_coverage_weighted_ratio": 1.0,
            "source_covered_chars": 0,
            "source_uncovered_samples": [],
            "source_coverage_findings": [],
            "source_coverage_category_summary": source_coverage_summary([]),
            "source_critical_missing_count": 0,
        }

    raw_units = [
        part.strip()
        for part in re.split(r"[\n。！？!?；;]+", source_text)
        if part.strip()
    ]
    units: list[str] = []
    for raw in raw_units:
        compact = compact_for_coverage(raw)
        if len(compact) < 6:
            continue
        if len(compact) > 80:
            for idx in range(0, len(compact), 60):
                unit = compact[idx:idx + 80]
                if len(unit) >= 6:
                    units.append(unit)
        else:
            units.append(compact)

    if not units:
        units = [source_compact[idx:idx + 80] for idx in range(0, len(source_compact), 80)]

    covered_chars = 0
    uncovered: list[str] = []
    for unit in units:
        if not unit:
            continue
        probe = unit if len(unit) <= 24 else unit[:24]
        if probe and probe in generated_compact:
            covered_chars += len(unit)
            continue
        window_hit = False
        if len(unit) > 24:
            for start in range(0, max(1, len(unit) - 23), 12):
                if unit[start:start + 24] in generated_compact:
                    window_hit = True
                    break
        if window_hit:
            covered_chars += len(unit)
        elif len(uncovered) < 5:
            uncovered.append(unit[:80])

    total = sum(len(unit) for unit in units) or len(source_compact)
    text_ratio = min(1.0, covered_chars / max(total, 1))
    points = source_coverage_points(source_text)
    for point in points:
        point["covered"] = source_point_covered(point, generated_compact)
    total_weight = sum(float(point.get("weight") or 0) for point in points)
    covered_weight = sum(float(point.get("weight") or 0) for point in points if point.get("covered"))
    weighted_ratio = covered_weight / total_weight if total_weight else text_ratio
    uncovered_points = [point for point in points if not point.get("covered")]
    severity_order = {"high": 0, "medium": 1, "low": 2}
    uncovered_points.sort(key=lambda point: (severity_order.get(point.get("severity"), 3), -float(point.get("weight") or 0)))
    findings = [
        {
            "category": point.get("category"),
            "category_label": point.get("category_label"),
            "severity": point.get("severity"),
            "weight": point.get("weight"),
            "text": point.get("text"),
        }
        for point in uncovered_points[:12]
    ]
    ratio = min(text_ratio, weighted_ratio) if points else text_ratio
    return {
        "source_char_count": len(source_text),
        "generated_text_char_count": len(generated_text),
        "source_coverage_ratio": round(ratio, 4),
        "source_coverage_text_ratio": round(text_ratio, 4),
        "source_coverage_weighted_ratio": round(weighted_ratio, 4),
        "source_covered_chars": covered_chars,
        "source_uncovered_samples": [item.get("text") for item in findings[:5]] or uncovered,
        "source_coverage_findings": findings,
        "source_coverage_category_summary": source_coverage_summary(points),
        "source_critical_missing_count": sum(1 for point in uncovered_points if point.get("severity") == "high"),
    }


def append_source_coverage_issues(report: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    ratio = float(report.get("source_coverage_ratio") or 0)
    source_chars = int(report.get("source_char_count") or 0)
    critical_missing = int(report.get("source_critical_missing_count") or 0)
    findings = report.get("source_coverage_findings") or []
    if source_chars >= 80 and critical_missing:
        samples = [
            f"{item.get('category_label') or '信息点'}：{item.get('text')}"
            for item in findings[:5]
            if isinstance(item, dict)
        ]
        issues.append({
            "severity": "warning",
            "code": "missing_source_information_points",
            "message": f"可能遗漏 {critical_missing} 个高权重原文信息点，优先检查对白、数字或关键线索。",
            "coverage_ratio": ratio,
            "samples": samples,
        })
    if source_chars >= 120 and ratio < 0.55:
        issues.append({
            "severity": "warning",
            "code": "low_source_coverage",
            "message": f"加权原文覆盖率约 {round(ratio * 100)}%，可能存在正文遗漏或过度概括。",
            "coverage_ratio": ratio,
            "samples": report.get("source_uncovered_samples") or [],
        })


def validate_chapter_script(
    chapter: dict[str, Any],
    entries: list[dict[str, Any]],
    character_book: dict[str, Any],
    parse_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    chapter_id = str(chapter.get("chapter_id") or "")
    lookup = character_lookup(character_book)
    known_speakers = {name for name in lookup.values() if name}
    known_speakers.add("NARRATOR")
    issues: list[dict[str, Any]] = []

    for issue in parse_issues:
        if not isinstance(issue, dict):
            continue
        issues.append({
            "severity": issue.get("level") or "warning",
            "code": "tagged_parse",
            "message": str(issue.get("message") or "Tagged script parse issue"),
            "line": issue.get("line"),
        })

    for idx, entry in enumerate(entries):
        speaker = str(entry.get("speaker") or "").strip()
        text = str(entry.get("text") or "").strip()
        instruct = str(entry.get("instruct") or "").strip()
        if not text:
            issues.append({"severity": "error", "code": "empty_text", "message": "脚本条目文本为空。", "entry_index": idx})
        if not speaker:
            issues.append({"severity": "error", "code": "empty_speaker", "message": "脚本条目缺少 speaker。", "entry_index": idx})
        elif speaker not in known_speakers:
            issues.append({
                "severity": "warning",
                "code": "unknown_speaker",
                "message": f"说话人「{speaker}」不在角色表中。",
                "entry_index": idx,
                "speaker": speaker,
            })
        if not instruct:
            issues.append({
                "severity": "info",
                "code": "missing_instruct",
                "message": "脚本条目缺少 instruct，TTS 表演可能不稳定。",
                "entry_index": idx,
                "speaker": speaker,
            })
        if len(text) > MAX_CHUNK_CHARS:
            issues.append({
                "severity": "warning",
                "code": "long_text",
                "message": f"单条文本 {len(text)} 字，可能过长。",
                "entry_index": idx,
                "speaker": speaker,
            })
        if entry.get("chapter_id") != chapter_id:
            issues.append({
                "severity": "error",
                "code": "chapter_mismatch",
                "message": "脚本条目的 chapter_id 与当前章节不一致。",
                "entry_index": idx,
            })

    coverage = source_coverage_report(str(chapter.get("content") or ""), entries)
    append_source_coverage_issues(coverage, issues)

    unknown_speakers = sorted({
        issue.get("speaker")
        for issue in issues
        if issue.get("code") == "unknown_speaker" and issue.get("speaker")
    })
    return {
        "chapter_id": chapter_id,
        "chapter_index": chapter.get("index"),
        "chapter_title": chapter.get("title") or chapter_id,
        "issue_count": len(issues),
        "error_count": sum(1 for issue in issues if issue.get("severity") == "error"),
        "warning_count": sum(1 for issue in issues if issue.get("severity") == "warning"),
        "unknown_speaker_count": len(unknown_speakers),
        "unknown_speakers": unknown_speakers,
        **coverage,
        "issues": issues,
        "updated_at": now_iso(),
    }


def chapter_memory_prompt(
    character_book: dict[str, Any],
    chapter: dict[str, Any],
    entries: list[dict[str, Any]],
    context: dict[str, Any],
) -> tuple[str, str]:
    system = "你是有声书制作系统的连续性记录员。只输出 JSON 对象，不要 Markdown，不要解释。"
    compact_entries = [
        {
            "speaker": entry.get("speaker"),
            "text": str(entry.get("text") or "")[:220],
            "instruct": entry.get("instruct") or "",
        }
        for entry in entries[:80]
    ]
    user = (
        f"{CHAPTER_MEMORY_STABLE_USER_PREAMBLE}\n\n"
        "当前章节输入（以下内容每次调用会变化，不属于缓存稳定前缀）：\n\n"
        f"前文记忆：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"角色表：\n{json.dumps(character_book, ensure_ascii=False, indent=2)}\n\n"
        f"当前章节：{chapter.get('title')}\n{chapter.get('content')}\n\n"
        f"本章脚本条目：\n{json.dumps(compact_entries, ensure_ascii=False, indent=2)}\n"
    )
    return system, user


MAX_CHUNK_CHARS = 500
CHAPTER_META_FIELDS = ("chapter_id", "chapter_index", "chapter_title")


def get_speaker(entry: dict[str, Any]) -> str:
    return str(entry.get("speaker") or entry.get("type") or "")


def entry_chapter_meta(entry: dict[str, Any]) -> dict[str, Any]:
    return {field: entry.get(field) for field in CHAPTER_META_FIELDS if entry.get(field) is not None}


def is_structural_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return len(stripped) < 80 and stripped[-1] not in ".!?。！？"


def make_chunk(speaker: str, text: str, instruct: str, chapter_meta: dict[str, Any]) -> dict[str, Any]:
    chunk = {"speaker": speaker, "text": text, "instruct": instruct}
    chunk.update(chapter_meta)
    return chunk


def group_entries_into_chunks(script_entries: list[dict[str, Any]], max_chars: int = MAX_CHUNK_CHARS) -> list[dict[str, Any]]:
    if not script_entries:
        return []

    chunks: list[dict[str, Any]] = []
    current_speaker = get_speaker(script_entries[0])
    current_text = str(script_entries[0].get("text") or "")
    current_instruct = str(script_entries[0].get("instruct") or "")
    current_chapter_meta = entry_chapter_meta(script_entries[0])

    for entry in script_entries[1:]:
        speaker = get_speaker(entry)
        text = str(entry.get("text") or "")
        instruct = str(entry.get("instruct") or "")
        chapter_meta = entry_chapter_meta(entry)
        can_merge = (
            speaker == current_speaker
            and instruct == current_instruct
            and chapter_meta == current_chapter_meta
            and not is_structural_text(current_text)
            and not is_structural_text(text)
        )
        if can_merge and len(current_text + " " + text) <= max_chars:
            current_text = current_text + " " + text
            continue

        chunks.append(make_chunk(current_speaker, current_text, current_instruct, current_chapter_meta))
        current_speaker = speaker
        current_text = text
        current_instruct = instruct
        current_chapter_meta = chapter_meta

    chunks.append(make_chunk(current_speaker, current_text, current_instruct, current_chapter_meta))
    return chunks


def initialize_new_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for idx, chunk in enumerate(chunks):
        chunk["id"] = idx
        chunk["status"] = "pending"
        chunk["audio_path"] = None
    return chunks


def sync_chunks(
    workspace_dir: Path,
    final_entries: list[dict[str, Any]],
    selected_chapter_ids: set[str],
) -> None:
    chunks_path = workspace_dir / "chunks.json"
    fresh_chunks = group_entries_into_chunks(final_entries)

    if not selected_chapter_ids:
        write_json(chunks_path, initialize_new_chunks(fresh_chunks))
        return

    existing_chunks = read_json(chunks_path, [])
    if not isinstance(existing_chunks, list) or not existing_chunks:
        write_json(chunks_path, initialize_new_chunks(fresh_chunks))
        return

    old_by_chapter: dict[str, list[dict[str, Any]]] = {}
    old_unscoped: list[dict[str, Any]] = []
    for chunk in existing_chunks:
        if not isinstance(chunk, dict):
            continue
        chapter_id = str(chunk.get("chapter_id") or "")
        if chapter_id:
            old_by_chapter.setdefault(chapter_id, []).append(chunk)
        else:
            old_unscoped.append(chunk)

    fresh_by_chapter: dict[str, list[dict[str, Any]]] = {}
    chapter_order: list[str] = []
    for chunk in fresh_chunks:
        chapter_id = str(chunk.get("chapter_id") or "")
        if not chapter_id:
            old_unscoped.append(chunk)
            continue
        if chapter_id not in fresh_by_chapter:
            chapter_order.append(chapter_id)
        fresh_by_chapter.setdefault(chapter_id, []).append(chunk)

    merged_chunks: list[dict[str, Any]] = []
    for chapter_id in chapter_order:
        if chapter_id in selected_chapter_ids:
            merged_chunks.extend(fresh_by_chapter.get(chapter_id, []))
        else:
            merged_chunks.extend(old_by_chapter.get(chapter_id) or fresh_by_chapter.get(chapter_id, []))

    known_ids = set(chapter_order)
    for chapter_id, chunks in old_by_chapter.items():
        if chapter_id not in known_ids and chapter_id not in selected_chapter_ids:
            merged_chunks.extend(chunks)
    merged_chunks.extend(old_unscoped)

    for idx, chunk in enumerate(merged_chunks):
        chunk["id"] = idx
        if chunk.get("chapter_id") in selected_chapter_ids:
            chunk.setdefault("status", "pending")
            chunk["status"] = "pending"
            chunk["audio_path"] = None
        else:
            chunk.setdefault("status", "pending")
            chunk.setdefault("audio_path", None)

    write_json(chunks_path, merged_chunks)


def load_chapters(workspace_dir: Path, input_path: Path) -> list[dict[str, Any]]:
    manifest_path = workspace_dir / "chapters" / "manifest.json"
    manifest = read_json(manifest_path, {})
    chapters = []
    for item in manifest.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        rel_path = item.get("path") or f"chapters/{item.get('filename', '')}"
        chapter_path = workspace_dir / rel_path
        if not chapter_path.exists():
            continue
        chapter = dict(item)
        chapter["content"] = read_text(chapter_path)
        chapters.append(chapter)
    if chapters:
        return chapters
    return split_text_into_chapters(read_text(input_path))


def default_annotation_prompt_templates() -> tuple[str, str]:
    fallback_system = (
        "你是有声书脚本化标注器。只输出 tagged 文本，不要 JSON，不要 Markdown，不要解释。"
    )
    fallback_user = (
        "任务：把当前小说章节忠实转换成适合 TTS 演播的 tagged 标注脚本。\n"
        "输出格式：每条一行，必须是 <说话人:>文本，可选在行尾追加 {instruct=声音/情绪描述}。\n"
        "示例：\n"
        "<旁白:>夜色沉了下来。 {instruct=稳定叙事}\n"
        "<龙傲天:>我来了。 {instruct=坚定，压低声音}\n\n"
        "规则：\n"
        "1. 旁白、动作、心理、环境、章节标题全部使用 <旁白:>。\n"
        "2. 只有明确说出口的对话才使用角色 canonical 名称。\n"
        "3. 不确定说话人时使用 <旁白:>，不要激进猜测。\n"
        "4. 保留原文语义、叙事人称和措辞；不要新增剧情、不要总结、不要把心理描写改成角色说出口。\n"
        "5. 可去掉最外层对话引号，但不要丢失话语内容；归因词和动作可拆为旁白或转入 instruct。\n"
        "6. 为朗读效果可拆分过长段落、补充简短 instruct、处理明显不适合朗读的符号。\n"
        "7. instruct 用简短中文描述声音/情绪/节奏，旁白默认稳定叙事。\n"
        "8. 每段正文都必须归入某个标签；不要输出列表编号、代码块或说明文字。\n\n"
        "前文连续性上下文：\n{context}\n\n"
        "可用角色表：\n{character_book}\n\n"
        "当前章节：{chapter_title}\n"
        "{chunk}\n"
    )
    try:
        return load_default_prompts()
    except RuntimeError:
        return fallback_system, fallback_user


def is_legacy_json_annotation_prompt(system_prompt: Any, user_prompt: Any) -> bool:
    combined = f"{system_prompt or ''}\n{user_prompt or ''}".casefold()
    if "output only valid json arrays" in combined:
        return True
    return '"speaker"' in combined and '"text"' in combined and '"instruct"' in combined


def normalize_annotation_prompt_template(user_prompt: str) -> str:
    """Keep custom static rules but move changing placeholders behind a stable preamble."""
    text = str(user_prompt or "").strip()
    if ANNOTATION_CACHE_PROMPT_MARKER in text:
        return text

    custom_static = text
    for placeholder, label in ANNOTATION_PLACEHOLDER_LABELS.items():
        custom_static = custom_static.replace(placeholder, f"[{label}见后文动态输入区]")
    custom_static = re.sub(r"\n{3,}", "\n\n", custom_static).strip()

    parts = [ANNOTATION_CACHE_STABLE_USER_PREAMBLE]
    if custom_static:
        parts.append(
            "项目自定义补充规则（已移除动态占位符以保持缓存稳定前缀）：\n"
            f"{custom_static}"
        )
    parts.append(ANNOTATION_DYNAMIC_INPUT_TEMPLATE)
    return "\n\n".join(parts)


def normalize_annotation_system_prompt(system_prompt: str) -> str:
    text = str(system_prompt or "").strip()
    if not text:
        return "你是有声书脚本化标注器。只输出 tagged 文本，不要 JSON，不要 Markdown，不要解释。"
    for placeholder, label in ANNOTATION_PLACEHOLDER_LABELS.items():
        text = text.replace(placeholder, f"[{label}见 user message 的动态输入区]")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def render_annotation_prompt_template(
    system_prompt: str,
    user_prompt: str,
    character_book: dict[str, Any],
    chapter: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, str]:
    replacements = {
        "{context}": json.dumps(context, ensure_ascii=False, indent=2),
        "{character_book}": json.dumps(character_book, ensure_ascii=False, indent=2),
        "{chapter_title}": str(chapter.get("title") or ""),
        "{chapter_content}": str(chapter.get("content") or ""),
        "{chunk}": str(chapter.get("content") or ""),
        "{source_text}": str(chapter.get("content") or ""),
    }
    system = normalize_annotation_system_prompt(system_prompt)
    user = normalize_annotation_prompt_template(user_prompt)
    for placeholder, value in replacements.items():
        system = system.replace(placeholder, value)
        user = user.replace(placeholder, value)
    return system, user


def character_analysis_prompt(character_book: dict[str, Any], chapter: dict[str, Any]) -> tuple[str, str]:
    system = (
        "你是有声书制作系统中的角色分析器。只输出 JSON 对象。"
        "你维护一本全局 character_book，用于后续说话人标注和音色分配。"
    )
    user = (
        f"{CHARACTER_ANALYSIS_STABLE_USER_PREAMBLE}\n\n"
        "当前章节输入（以下内容每次调用会变化，不属于缓存稳定前缀）：\n\n"
        f"已有 character_book：\n{json.dumps(character_book, ensure_ascii=False, indent=2)}\n\n"
        f"当前章节：{chapter.get('title')}\n"
        f"{chapter.get('content')}\n"
    )
    return system, user


def annotation_prompt(
    character_book: dict[str, Any],
    chapter: dict[str, Any],
    context: dict[str, Any],
    prompts_config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    default_system, default_user = default_annotation_prompt_templates()
    prompts_config = prompts_config if isinstance(prompts_config, dict) else {}
    system_template = str(prompts_config.get("system_prompt") or default_system)
    user_template = str(prompts_config.get("user_prompt") or default_user)
    if is_legacy_json_annotation_prompt(system_template, user_template):
        system_template, user_template = default_system, default_user
    return render_annotation_prompt_template(system_template, user_template, character_book, chapter, context)


def save_entries(workspace_dir: Path, entries: list[dict[str, Any]], partial: bool = False) -> Path:
    path = workspace_dir / ("annotated_script.partial.json" if partial else "annotated_script.json")
    write_json(path, entries)
    return path


def script_speakers(entries: list[dict[str, Any]]) -> list[str]:
    speakers: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        speaker = str(entry.get("speaker") or entry.get("type") or "").strip()
        if not speaker:
            continue
        speaker = "NARRATOR" if speaker.upper() in {"NARRATOR", "旁白"} else speaker
        key = speaker.casefold()
        if key in seen:
            continue
        seen.add(key)
        speakers.append(speaker)
    return speakers


def character_book_speakers(character_book: dict[str, Any]) -> list[str]:
    speakers: list[str] = []
    if character_book.get("characters") or str(character_book.get("narrator_style") or "").strip():
        speakers.append("NARRATOR")
    for character in character_book.get("characters") or []:
        if not isinstance(character, dict):
            continue
        canonical = str(character.get("canonical") or "").strip()
        if canonical:
            speakers.append(canonical)
    return speakers


def has_reusable_character_book(character_book: dict[str, Any]) -> bool:
    return any(speaker != "NARRATOR" for speaker in character_book_speakers(character_book))


def merge_speakers(*speaker_lists: list[str]) -> list[str]:
    speakers: list[str] = []
    seen: set[str] = set()
    for values in speaker_lists:
        for speaker in values or []:
            speaker = str(speaker or "").strip()
            if not speaker:
                continue
            speaker = "NARRATOR" if speaker.upper() in {"NARRATOR", "旁白"} else speaker
            key = speaker.casefold()
            if key in seen:
                continue
            seen.add(key)
            speakers.append(speaker)
    return speakers


def normalize_voice_config_item(config: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(config, dict):
        return {}, False
    normalized = dict(config)
    changed = False
    if normalized.get("type") == "dashscope":
        model = normalized.get("dashscope_model") or "qwen3-tts-instruct-flash"
        if normalized.get("dashscope_model") != model:
            normalized["dashscope_model"] = model
            changed = True
    elif normalized.get("type") == "volcengine":
        defaults = {
            "volcengine_resource_id": "seed-tts-2.0",
            "volcengine_sample_rate": 24000,
            "volcengine_speech_rate": 0,
            "volcengine_loudness_rate": 0,
            "volcengine_emotion_scale": 4,
        }
        for key, value in defaults.items():
            if normalized.get(key) in (None, ""):
                normalized[key] = value
                changed = True
    return normalized, changed


def voice_config_has_required_choice(config: Any) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    config_type = str(config.get("type") or "custom")
    if config_type == "custom":
        return bool(str(config.get("voice") or "").strip())
    if config_type == "edge":
        return bool(str(config.get("edge_voice") or "").strip())
    if config_type == "dashscope":
        return bool(str(config.get("dashscope_voice") or "").strip())
    if config_type == "volcengine":
        return bool(str(config.get("volcengine_speaker") or "").strip())
    if config_type == "clone":
        return bool(str(config.get("ref_audio") or "").strip() and str(config.get("ref_text") or "").strip())
    if config_type in {"builtin_lora", "lora"}:
        return bool(str(config.get("adapter_id") or config.get("adapter_path") or "").strip())
    if config_type == "design":
        return bool(str(config.get("description") or "").strip())
    return True


def voice_config_effective_signature(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    normalized, _ = normalize_voice_config_item(config)
    style = str(normalized.get("character_style") or normalized.get("default_style") or "").strip()
    normalized["character_style"] = style
    normalized.pop("default_style", None)
    normalized.pop("confirmed", None)
    config_type = str(normalized.get("type") or "custom")
    fields_by_type = {
        "custom": ("type", "voice", "character_style", "seed"),
        "dashscope": ("type", "dashscope_model", "dashscope_voice", "character_style", "seed"),
        "volcengine": (
            "type",
            "volcengine_resource_id",
            "volcengine_speaker",
            "volcengine_sample_rate",
            "volcengine_speech_rate",
            "volcengine_loudness_rate",
            "volcengine_emotion",
            "volcengine_emotion_scale",
            "character_style",
        ),
        "edge": ("type", "edge_voice", "edge_rate", "edge_pitch"),
        "clone": ("type", "ref_audio", "ref_text", "character_style", "seed"),
        "builtin_lora": ("type", "adapter_id", "adapter_path", "character_style", "seed"),
        "lora": ("type", "adapter_id", "adapter_path", "character_style", "seed"),
        "design": ("type", "description", "seed"),
    }
    fields = fields_by_type.get(config_type, tuple(sorted(normalized.keys())))
    return {
        key: normalized.get(key)
        for key in fields
        if normalized.get(key) not in (None, "", [], {})
    }


def legacy_auto_voice_config_for_speaker(speaker: str, character_book: dict[str, Any]) -> dict[str, Any]:
    speaker = str(speaker or "").strip()
    config: dict[str, Any] = {"type": "custom", "voice": "Ryan", "seed": "-1"}
    style = ""
    if speaker.upper() == "NARRATOR":
        style = str(character_book.get("narrator_style") or "").strip()
    else:
        for character in character_book.get("characters") or []:
            if not isinstance(character, dict):
                continue
            canonical = str(character.get("canonical") or "").strip()
            aliases = character.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            names = {canonical.casefold(), *(str(alias).strip().casefold() for alias in aliases if str(alias).strip())}
            if speaker.casefold() in names:
                style = character_voice_style(character)
                break
    if style:
        config["character_style"] = style
    return config


def is_legacy_unconfirmed_auto_voice_config(
    speaker: str,
    config: Any,
    character_book: dict[str, Any],
) -> bool:
    if not isinstance(config, dict) or config.get("confirmed"):
        return False
    if str(config.get("type") or "custom") != "custom":
        return False
    legacy_auto = legacy_auto_voice_config_for_speaker(speaker, character_book)
    return voice_config_effective_signature(config) == voice_config_effective_signature(legacy_auto)


def sync_voice_config(
    workspace_dir: Path,
    entries: list[dict[str, Any]],
    character_book: dict[str, Any],
) -> dict[str, Any]:
    voice_config_path = workspace_dir / "voice_config.json"
    voice_config = read_json(voice_config_path, {})
    if not isinstance(voice_config, dict):
        voice_config = {}
    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    cleaned: dict[str, dict[str, Any]] = {}
    for speaker, existing in voice_config.items():
        speaker = str(speaker or "").strip()
        if not speaker or not isinstance(existing, dict) or not existing:
            if speaker:
                removed.append(speaker)
            continue
        normalized, changed = normalize_voice_config_item(existing)
        if (
            not voice_config_has_required_choice(normalized)
            or is_legacy_unconfirmed_auto_voice_config(speaker, normalized, character_book)
        ):
            removed.append(speaker)
            continue
        cleaned[speaker] = normalized
        if changed:
            updated.append(speaker)
    voice_config = cleaned
    for speaker in merge_speakers(script_speakers(entries), character_book_speakers(character_book)):
        existing = voice_config.get(speaker)
        if not isinstance(existing, dict) or not existing:
            continue
    if added or updated or removed:
        if voice_config:
            write_json(voice_config_path, voice_config)
        elif voice_config_path.exists():
            voice_config_path.unlink()
    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "total": len(voice_config),
    }


def delete_old_chunks(workspace_dir: Path) -> None:
    chunks_path = workspace_dir / "chunks.json"
    if chunks_path.exists():
        chunks_path.unlink()


def parse_chapter_ids(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def merge_generated_chapters(
    existing_entries: Any,
    generated_by_chapter: dict[str, list[dict[str, Any]]],
    all_chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(existing_entries, list):
        existing_entries = []

    manifest_ids = [str(chapter.get("chapter_id") or "") for chapter in all_chapters]
    manifest_id_set = {chapter_id for chapter_id in manifest_ids if chapter_id}
    existing_by_chapter: dict[str, list[dict[str, Any]]] = {}
    trailing_entries: list[dict[str, Any]] = []

    for entry in existing_entries:
        if not isinstance(entry, dict):
            continue
        chapter_id = str(entry.get("chapter_id") or "")
        if chapter_id and chapter_id in manifest_id_set:
            existing_by_chapter.setdefault(chapter_id, []).append(entry)
        else:
            trailing_entries.append(entry)

    merged: list[dict[str, Any]] = []
    for chapter_id in manifest_ids:
        if not chapter_id:
            continue
        if chapter_id in generated_by_chapter:
            merged.extend(generated_by_chapter[chapter_id])
        else:
            merged.extend(existing_by_chapter.get(chapter_id, []))

    merged.extend(trailing_entries)
    return merged


def target_chapter_ids_for_checkpoint(
    selected_chapter_ids: set[str],
    all_chapters: list[dict[str, Any]],
) -> set[str]:
    if selected_chapter_ids:
        return selected_chapter_ids
    return {
        str(chapter.get("chapter_id") or "")
        for chapter in all_chapters
        if str(chapter.get("chapter_id") or "")
    }


def merge_checkpoint_entries(
    existing_entries: Any,
    generated_by_chapter: dict[str, list[dict[str, Any]]],
    all_chapters: list[dict[str, Any]],
    target_chapter_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(existing_entries, list):
        existing_entries = []
    filtered_existing = [
        entry for entry in existing_entries
        if not (
            isinstance(entry, dict)
            and str(entry.get("chapter_id") or "") in target_chapter_ids
        )
    ]
    return merge_generated_chapters(filtered_existing, generated_by_chapter, all_chapters)


def checkpoint_script_outputs(
    workspace_dir: Path,
    existing_entries: Any,
    generated_by_chapter: dict[str, list[dict[str, Any]]],
    all_chapters: list[dict[str, Any]],
    target_chapter_ids: set[str],
    character_book: dict[str, Any],
) -> dict[str, Any]:
    final_entries = merge_checkpoint_entries(
        existing_entries,
        generated_by_chapter,
        all_chapters,
        target_chapter_ids,
    )
    output_path = save_entries(workspace_dir, final_entries, partial=False)
    sync_chunks(workspace_dir, final_entries, target_chapter_ids)
    voice_defaults = sync_voice_config(workspace_dir, final_entries, character_book)
    return {
        "entries": final_entries,
        "output_path": output_path,
        "voice_defaults": voice_defaults,
    }


def mark_following_memory_stale(
    workspace_dir: Path,
    memory: dict[str, Any],
    all_chapters: list[dict[str, Any]],
    selected_chapter_ids: set[str],
) -> dict[str, Any]:
    if not selected_chapter_ids:
        return memory
    chapter_order = [str(chapter.get("chapter_id") or "") for chapter in all_chapters]
    selected_indexes = [idx for idx, chapter_id in enumerate(chapter_order) if chapter_id in selected_chapter_ids]
    if not selected_indexes:
        return memory
    first_selected_index = min(selected_indexes)
    chapters_memory = memory.get("chapters") if isinstance(memory.get("chapters"), dict) else {}
    for chapter_id in chapter_order[first_selected_index + 1:]:
        item = chapters_memory.get(chapter_id)
        if isinstance(item, dict):
            item["stale"] = True
            item["stale_reason"] = "前序章节已重跑，建议从该处继续重跑以刷新连续性记忆。"
            item["stale_since"] = now_iso()
    memory["chapters"] = chapters_memory
    save_chapter_memory(workspace_dir, memory)
    return memory


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)

    parser = argparse.ArgumentParser(description="Generate an annotated audiobook script by chapter")
    parser.add_argument("input_file_path", help="Path to the book text file")
    parser.add_argument(
        "--workspace-dir",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        help="Directory where book-local files are stored",
    )
    parser.add_argument(
        "--chapter-ids",
        default="",
        help="Comma-separated chapter IDs to generate. Empty means generate the full book.",
    )
    parser.add_argument(
        "--mode",
        choices=["script", "characters"],
        default="script",
        help="script generates tagged script and chunks; characters only updates the character pool.",
    )
    parser.add_argument(
        "--reuse-character-book",
        action="store_true",
        help="Use the existing character_book.json and skip per-chapter character analysis.",
    )
    parser.add_argument(
        "--enable-chapter-memory",
        action="store_true",
        help="Generate per-chapter memory summaries after tagged script generation.",
    )
    args = parser.parse_args()
    mode = args.mode
    engine = "character_pipeline" if mode == "characters" else "chapter_pipeline"
    reuse_character_book = bool(args.reuse_character_book and mode == "script")
    enable_chapter_memory = bool(args.enable_chapter_memory and mode == "script")

    app_dir = Path(__file__).resolve().parent
    config = read_json(app_dir / "config.json", {})
    prompts_config = config.get("prompts") if isinstance(config.get("prompts"), dict) else {}
    workspace_dir = Path(args.workspace_dir).resolve()
    input_path = Path(args.input_file_path).resolve()
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", flush=True)
        sys.exit(1)

    chapters = load_chapters(workspace_dir, input_path)
    if not chapters:
        print("Error: No chapters found", flush=True)
        sys.exit(1)
    all_chapters = chapters
    selected_chapter_ids = parse_chapter_ids(args.chapter_ids)
    if selected_chapter_ids:
        known_ids = {str(chapter.get("chapter_id") or "") for chapter in all_chapters}
        missing_ids = sorted(selected_chapter_ids - known_ids)
        if missing_ids:
            print(f"Error: Unknown chapter IDs: {', '.join(missing_ids)}", flush=True)
            sys.exit(1)
        chapters = [chapter for chapter in all_chapters if str(chapter.get("chapter_id") or "") in selected_chapter_ids]
    target_chapter_ids = target_chapter_ids_for_checkpoint(selected_chapter_ids, all_chapters)

    model_name = resolve_model_name(config)
    provider = resolve_provider(config)
    openai_api_type = resolve_openai_api_type(config) if provider == "openai" else ""
    total_chars = sum(len(chapter.get("content") or "") for chapter in chapters)
    init_generation_state(
        workspace_dir,
        chapters,
        selected_chapter_ids,
        model_name,
        provider,
        mode,
        reuse_character_book,
        enable_chapter_memory,
    )
    emit(
        "init",
        input_file=str(input_path),
        char_count=total_chars,
        chapter_count=len(chapters),
        selected_chapter_ids=sorted(selected_chapter_ids),
        model=model_name,
        provider=provider,
        openai_api_type=openai_api_type,
        engine=engine,
        mode=mode,
        reuse_character_book=reuse_character_book,
        enable_chapter_memory=enable_chapter_memory,
    )

    character_book_path = workspace_dir / "character_book.json"
    character_book = normalize_character_book(read_json(character_book_path, default_character_book()))
    if reuse_character_book and not has_reusable_character_book(character_book):
        message = "当前人物池为空。请先分析人物池，或导入/新增角色后再使用 --reuse-character-book。"
        finish_generation_state(workspace_dir, "error", error=message)
        emit("error", message=message)
        print(f"Error: {message}", flush=True)
        sys.exit(1)

    try:
        model = build_chat_model(config)
    except Exception as exc:
        finish_generation_state(workspace_dir, "error", error=str(exc))
        emit("error", message=str(exc))
        print(f"Error: {exc}", flush=True)
        sys.exit(1)

    chapter_memory = load_chapter_memory(workspace_dir)
    script_issues = load_script_issues(workspace_dir)
    if mode == "script" and selected_chapter_ids:
        chapter_memory = mark_following_memory_stale(workspace_dir, chapter_memory, all_chapters, selected_chapter_ids)
        for selected_id in selected_chapter_ids:
            if isinstance(chapter_memory.get("chapters"), dict):
                chapter_memory["chapters"].pop(selected_id, None)
            if isinstance(script_issues.get("chapters"), dict):
                script_issues["chapters"].pop(selected_id, None)
        save_chapter_memory(workspace_dir, chapter_memory)
        save_script_issues(workspace_dir, script_issues)
    all_entries: list[dict[str, Any]] = []
    generated_by_chapter: dict[str, list[dict[str, Any]]] = {}
    completed_chapter_ids: set[str] = set()
    existing_entries = read_json(workspace_dir / "annotated_script.json", [])
    all_context_entries = existing_entries if isinstance(existing_entries, list) else []

    for idx, chapter in enumerate(chapters, start=1):
        if _cancel_flag["stop"]:
            if mode == "characters":
                voice_defaults = sync_voice_config(workspace_dir, [], character_book)
                emit(
                    "cancelled",
                    mode=mode,
                    engine=engine,
                    characters=len(character_book.get("characters") or []),
                    completed_chapter_ids=sorted(completed_chapter_ids),
                    voice_config_added=len(voice_defaults.get("added") or []),
                    voice_config_updated=len(voice_defaults.get("updated") or []),
                    voice_config_total=voice_defaults.get("total", 0),
                )
                for chapter in chapters[idx - 1:]:
                    pending_id = str(chapter.get("chapter_id") or "")
                    if pending_id and pending_id not in completed_chapter_ids:
                        update_chapter_state(workspace_dir, pending_id, status="cancelled")
                        update_character_analysis_state(
                            workspace_dir,
                            chapter,
                            status="cancelled",
                            characters=len(character_book.get("characters") or []),
                        )
                finish_generation_state(
                    workspace_dir,
                    "cancelled",
                    mode=mode,
                    engine=engine,
                    character_count=len(character_book.get("characters") or []),
                    completed_chapter_ids=sorted(completed_chapter_ids),
                )
                sys.exit(0)
            if generated_by_chapter:
                checkpoint = checkpoint_script_outputs(
                    workspace_dir,
                    existing_entries,
                    generated_by_chapter,
                    all_chapters,
                    target_chapter_ids,
                    character_book,
                )
                final_entries = checkpoint["entries"]
                output_path = checkpoint["output_path"]
                voice_defaults = checkpoint["voice_defaults"]
                partial_path = save_entries(workspace_dir, all_entries, partial=True)
                emit(
                    "cancelled",
                    mode=mode,
                    engine=engine,
                    total_entries=len(final_entries),
                    generated_entries=len(all_entries),
                    completed_chapter_ids=sorted(generated_by_chapter),
                    output_path=str(output_path),
                    partial_path=str(partial_path),
                    voice_config_added=len(voice_defaults.get("added") or []),
                    voice_config_updated=len(voice_defaults.get("updated") or []),
                    voice_config_total=voice_defaults.get("total", 0),
                )
            else:
                partial_path = save_entries(workspace_dir, all_entries, partial=True)
                emit("cancelled", mode=mode, engine=engine, total_entries=len(all_entries), output_path=str(partial_path))
            for chapter in chapters[idx - 1:]:
                pending_id = str(chapter.get("chapter_id") or "")
                if pending_id and pending_id not in generated_by_chapter:
                    update_chapter_state(workspace_dir, pending_id, status="cancelled")
            finish_generation_state(
                workspace_dir,
                "cancelled",
                mode=mode,
                engine=engine,
                total_entries=len(all_entries),
                completed_chapter_ids=sorted(generated_by_chapter),
            )
            sys.exit(0)

        chapter_id = str(chapter.get("chapter_id") or f"chapter_{idx:04d}")
        chapter_title = str(chapter.get("title") or chapter_id)
        chapter_text = str(chapter.get("content") or "")
        update_chapter_state(
            workspace_dir,
            chapter_id,
            status="running",
            chapter_index=chapter.get("index") or idx,
            chapter_title=chapter_title,
            char_count=len(chapter_text),
            error="",
        )
        emit(
            "chapter_start",
            mode=mode,
            engine=engine,
            reuse_character_book=reuse_character_book,
            index=idx,
            total=len(chapters),
            chapter_id=chapter_id,
            title=chapter_title,
            char_count=len(chapter_text),
            preview=chapter_text[:500],
        )
        print(f"Processing chapter {idx}/{len(chapters)}: {chapter_title}", flush=True)

        character_analysis_started = False
        character_analysis_completed = False
        try:
            if reuse_character_book:
                emit(
                    "character_book_skipped",
                    mode=mode,
                    engine=engine,
                    index=idx,
                    total=len(chapters),
                    chapter_id=chapter_id,
                    title=chapter_title,
                    characters=len(character_book.get("characters") or []),
                )
            else:
                character_analysis_started = True
                update_character_analysis_state(
                    workspace_dir,
                    chapter,
                    status="running",
                    characters=len(character_book.get("characters") or []),
                )
                system, user = character_analysis_prompt(character_book, chapter)
                updated_book = invoke_json(model, system, user, expected="object", stage="characters")
                character_book = merge_character_books(character_book, updated_book)
                write_json(character_book_path, character_book)
                character_analysis_completed = True
                update_character_analysis_state(
                    workspace_dir,
                    chapter,
                    status="done",
                    characters=len(character_book.get("characters") or []),
                )
                emit(
                    "character_book_done",
                    mode=mode,
                    engine=engine,
                    index=idx,
                    total=len(chapters),
                    chapter_id=chapter_id,
                    title=chapter_title,
                    characters=len(character_book.get("characters") or []),
                )

            if mode == "characters":
                completed_chapter_ids.add(chapter_id)
                update_chapter_state(
                    workspace_dir,
                    chapter_id,
                    status="done",
                    entry_count=0,
                    parse_issues=0,
                    characters=len(character_book.get("characters") or []),
                    error="",
                )
                emit(
                    "chapter_done",
                    mode=mode,
                    engine=engine,
                    index=idx,
                    total=len(chapters),
                    chapter_id=chapter_id,
                    title=chapter_title,
                    entries=0,
                    total_entries=0,
                    parse_issues=0,
                    issue_count=0,
                    unknown_speaker_count=0,
                    characters=len(character_book.get("characters") or []),
                    sample=[],
                )
                continue

            chapter_meta = {
                "chapter_id": chapter_id,
                "chapter_index": int(chapter.get("index") or idx),
                "chapter_title": chapter_title,
            }
            continuity_context = recent_context_for_chapter(
                chapter_memory,
                all_chapters,
                chapter_id,
                all_context_entries,
            )
            system, user = annotation_prompt(character_book, chapter, continuity_context, prompts_config)
            entries, parse_issues, _tagged_text = invoke_tagged_entries(model, system, user, chapter_meta)
            if not entries:
                raise RuntimeError(f"No valid entries generated for chapter {chapter_id}")
            entries, speaker_updates = normalize_script_entries(entries, character_book)
        except Exception as exc:
            if character_analysis_started and not character_analysis_completed:
                update_character_analysis_state(
                    workspace_dir,
                    chapter,
                    status="error",
                    characters=len(character_book.get("characters") or []),
                    error=str(exc),
                )
            update_chapter_state(workspace_dir, chapter_id, status="error", error=str(exc))
            finish_generation_state(workspace_dir, "error", error=str(exc), failed_chapter_id=chapter_id)
            raise

        validation = validate_chapter_script(chapter, entries, character_book, parse_issues)
        script_issues.setdefault("chapters", {})[chapter_id] = validation
        save_script_issues(workspace_dir, script_issues)

        memory_issue = ""
        if enable_chapter_memory:
            try:
                system, user = chapter_memory_prompt(character_book, chapter, entries, continuity_context)
                memory_payload = invoke_json(model, system, user, expected="object", stage="memory")
                chapter_memory.setdefault("chapters", {})[chapter_id] = normalize_memory_payload(memory_payload, chapter, entries)
                save_chapter_memory(workspace_dir, chapter_memory)
                emit(
                    "chapter_memory_done",
                    mode=mode,
                    engine=engine,
                    index=idx,
                    total=len(chapters),
                    chapter_id=chapter_id,
                    title=chapter_title,
                )
            except Exception as exc:
                memory_issue = str(exc)
                validation.setdefault("issues", []).append({
                    "severity": "warning",
                    "code": "memory_generation_failed",
                    "message": f"章节记忆生成失败：{memory_issue}",
                })
                validation["issue_count"] = len(validation["issues"])
                validation["warning_count"] = validation.get("warning_count", 0) + 1
                validation["updated_at"] = now_iso()
                script_issues.setdefault("chapters", {})[chapter_id] = validation
                save_script_issues(workspace_dir, script_issues)
                emit(
                    "chapter_memory_error",
                    mode=mode,
                    engine=engine,
                    index=idx,
                    total=len(chapters),
                    chapter_id=chapter_id,
                    title=chapter_title,
                    message=memory_issue,
                )

        generated_by_chapter[chapter_id] = entries
        completed_chapter_ids.add(chapter_id)
        all_entries.extend(entries)
        checkpoint = checkpoint_script_outputs(
            workspace_dir,
            existing_entries,
            generated_by_chapter,
            all_chapters,
            target_chapter_ids,
            character_book,
        )
        all_context_entries = checkpoint["entries"]
        save_entries(workspace_dir, all_entries, partial=True)
        update_chapter_state(
            workspace_dir,
            chapter_id,
            status="done",
            entry_count=len(entries),
            parse_issues=len(parse_issues),
            issue_count=validation.get("issue_count", 0),
            error_count=validation.get("error_count", 0),
            warning_count=validation.get("warning_count", 0),
            unknown_speaker_count=validation.get("unknown_speaker_count", 0),
            source_coverage_ratio=validation.get("source_coverage_ratio"),
            source_char_count=validation.get("source_char_count", 0),
            generated_text_char_count=validation.get("generated_text_char_count", 0),
            speaker_updates=speaker_updates,
            memory_error=memory_issue,
            error="",
        )
        emit(
            "chapter_done",
            mode=mode,
            engine=engine,
            index=idx,
            total=len(chapters),
            chapter_id=chapter_id,
            title=chapter_title,
            entries=len(entries),
            total_entries=len(all_entries),
            parse_issues=len(parse_issues),
            issue_count=validation.get("issue_count", 0),
            unknown_speaker_count=validation.get("unknown_speaker_count", 0),
            source_coverage_ratio=validation.get("source_coverage_ratio"),
            sample=entries[:5],
        )

    if mode == "characters":
        voice_defaults = sync_voice_config(workspace_dir, [], character_book)
        speakers = character_book_speakers(character_book)
        emit(
            "done",
            mode=mode,
            engine=engine,
            reuse_character_book=reuse_character_book,
            enable_chapter_memory=enable_chapter_memory,
            total_entries=0,
            generated_entries=0,
            characters=len(character_book.get("characters") or []),
            speakers=speakers,
            output_path=str(character_book_path),
            selected_chapter_ids=sorted(selected_chapter_ids),
            voice_config_added=len(voice_defaults.get("added") or []),
            voice_config_updated=len(voice_defaults.get("updated") or []),
            voice_config_total=voice_defaults.get("total", 0),
        )
        finish_generation_state(
            workspace_dir,
            "done",
            mode=mode,
            engine=engine,
            total_entries=0,
            generated_entries=0,
            character_count=len(character_book.get("characters") or []),
            completed_chapter_ids=sorted(completed_chapter_ids),
        )
        return

    if not all_entries:
        print("Error: No script entries generated", flush=True)
        sys.exit(1)

    checkpoint = checkpoint_script_outputs(
        workspace_dir,
        existing_entries,
        generated_by_chapter,
        all_chapters,
        target_chapter_ids,
        character_book,
    )
    final_entries = checkpoint["entries"]
    output_path = checkpoint["output_path"]
    partial_path = workspace_dir / "annotated_script.partial.json"
    if partial_path.exists():
        partial_path.unlink()
    voice_defaults = checkpoint["voice_defaults"]
    speakers = sorted({str(entry.get("speaker") or "UNKNOWN") for entry in final_entries})
    emit(
        "done",
        mode=mode,
        engine=engine,
        reuse_character_book=reuse_character_book,
        enable_chapter_memory=enable_chapter_memory,
        total_entries=len(final_entries),
        generated_entries=len(all_entries),
        speakers=speakers,
        output_path=str(output_path),
        selected_chapter_ids=sorted(selected_chapter_ids),
        voice_config_added=len(voice_defaults.get("added") or []),
        voice_config_updated=len(voice_defaults.get("updated") or []),
        voice_config_total=voice_defaults.get("total", 0),
    )
    finish_generation_state(
        workspace_dir,
        "done",
        mode=mode,
        engine=engine,
        total_entries=len(final_entries),
        generated_entries=len(all_entries),
        completed_chapter_ids=sorted(generated_by_chapter),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("error", message=str(exc))
        print(f"Error: {exc}", flush=True)
        sys.exit(1)
