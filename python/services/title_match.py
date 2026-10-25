"""窗口标题漂移匹配（纯函数，无 win32 依赖，player 与 replay_gate 共用）。

录制标题与回放时的实际标题可能因应用状态后缀不同而"多字/少字"：
Everything 的 [管理员模式]、WPS 的 [只读]/扩展名显隐、未保存 * 标记等。
单向包含（录制 ⊆ 实际）此时失效，这里提供归一化拆分与带防误判的相似度评分：
应用后缀（标题尾段）不同直接拒绝；文档名过短（<4 字）须近全等，
避免"报告A"匹配到"报告B"这类同应用切错窗口。
"""
import difflib


def title_parts(title):
    """归一化标题并拆成 "主内容 - 应用后缀" 两段。"""
    norm = " ".join(str(title).strip().lstrip("*").split()).casefold()
    if " - " in norm:
        pref, suf = norm.rsplit(" - ", 1)
    else:
        pref, suf = norm, ""
    return norm, pref, suf


def _name_matches(a: str, b: str, min_sim: float, min_sim_len: int) -> bool:
    """短语级名称比对：相等/前缀追加(状态标记、扩展名)/高相似(长名小幅修改)。
    difflib 对短中文串不可靠（"演示程序 [增强模式]" vs "演示程序" 仅 0.53），
    前缀包含才是状态标记追加的正确语义。"""
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter):
        return True
    return len(shorter) >= min_sim_len and difflib.SequenceMatcher(
        None, shorter, longer).ratio() >= min_sim


def score_parts(rec_parts, w_parts):
    """录制标题与实际标题的相似度评分，0 表示不认。"""
    rec_norm, rec_pref, rec_suf = rec_parts
    w_norm, w_pref, w_suf = w_parts
    if len(rec_norm) < 6 or len(w_norm) < 6:
        return 0.0
    full = difflib.SequenceMatcher(None, rec_norm, w_norm).ratio()
    if full < 0.6:
        return 0.0
    if rec_suf and w_suf:
        # 应用后缀不同直接拒绝（同文档不同应用），状态标记追加除外
        if not _name_matches(rec_suf, w_suf, min_sim=0.6, min_sim_len=4):
            return 0.0
        if not rec_pref or not w_pref:
            return 0.0
        # 文档名：前缀追加（扩展名/状态尾巴）或长名（≥6字）小幅修改；
        # 短名（<6字）单字差异（报告A/报告B）必须拒绝，避免同应用切错文档
        if not _name_matches(rec_pref, w_pref, min_sim=0.8, min_sim_len=6):
            return 0.0
        return 1.5
    return 1.5 if full >= 0.75 else 0.0


def titles_similar(a, b) -> bool:
    """两个窗口标题是否可视为同一目标（标题漂移容忍）。"""
    return score_parts(title_parts(a), title_parts(b)) > 0
