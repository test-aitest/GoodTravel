import re
import unicodedata
from collections.abc import Iterable, Sequence
from html import unescape

WINDOW = 200

AI_READER = (
    r"(?:ai|llm|gpt)[\s-]*(?:assistant|agent|model|system|bot|tool|worker|reader|crawler)s?"
    r"|llms?"
    r"|(?:large\s+)?language\s+models?"
    r"|chatbots?"
    r"|(?:automated|autonomous)\s+(?:assistant|agent|system|tool|bot|reader)s?"
    r"|ai(?=\s*[:,;.!?]|\s+(?:reading|that|who|and)\b)"
)
JA_AI_READER = (
    r"(?:(?<![a-z0-9])(?:ai|llm|gpt)|エーアイ|人工知能|(?:大規模)?言語モデル|チャットボット)"
    r"(?:\s*(?:エージェント|アシスタント|モデル|システム|ボット|ツール|クローラー?|リーダー))?"
    r"|(?:自動化?された|自動|自律型?)\s*(?:エージェント|アシスタント|システム|ボット|ツール)"
)
ANY_AI_READER = rf"(?:{AI_READER}|{JA_AI_READER})"
JA_HONORIFIC = r"(?:さん|様|さま|殿|の(?:皆|みな)(?:さん|様|さま)|の方々|の方|各位|たち|達)"
JA_MESSAGE = r"(?:指示|命令|指令|メッセージ|お知らせ|通知|注意事項|注意|警告|お願い|連絡|メモ|伝言|リマインダー?)"
ADDRESS = re.compile(
    r"\b(?:(?:note|message|instructions?|notice|reminder|memo|warning)\s+(?:for|to)"
    r"|dear|hey|hi|hello|attention"
    r"|if\s+you\s+are|if\s+you're|you\s+are|you're)"
    rf"\s+(?:(?:any|all|every|the|an?|other)\s+)*(?:{AI_READER})\b"
    rf"|\b(?:{AI_READER})\s+reading\s+this\b"
    rf"|(?:^|(?<=[\n.!?;:]))\s*(?:{AI_READER})\s*[:,]"
    rf"|{ANY_AI_READER}\s*{JA_HONORIFIC}?\s*(?:への|に対する|に向けた|向けの?|宛ての?)\s*{JA_MESSAGE}"
    rf"|{ANY_AI_READER}\s*{JA_HONORIFIC}?\s*(?:へ|に告ぐ)[^\S\n]*[:,、!。\n]"
    rf"|{ANY_AI_READER}\s*{JA_HONORIFIC}[^\S\n]*[:,、!\n]"
    rf"|(?:^|(?<=[\n.!?;:。]))\s*{ANY_AI_READER}\s*:"
    rf"|(?:この|本)\s*(?:ページ|文章|文書|サイト|画面|テキスト|メール)\s*を\s*(?:読んで|読み込んで|処理して|解析して|見て)\s*いる\s*{ANY_AI_READER}"
    rf"|(?:あなた|貴方|君|きみ|お前)\s*(?:が|は)\s*{ANY_AI_READER}\s*(?:なら|であれば|の場合|だったら|でしたら)"
)
JA_TARGET = r"(?:指示|命令|指令|依頼|要求|リクエスト|タスク|仕事|作業|プロンプト|ルール|ガイドライン|承認|確認|レビュー)"
JA_SET_ASIDE = r"(?:無視|忘れ|破棄|取り消|取消|キャンセル|上書き|無効|飛ばし|スキップ|省略|回避|迂回)"
TARGET = r"(?:request|instruction|task|prompt|order|direction|rule|guideline|goal|job|approval|confirmation|review|user)s?"
OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass|skip)\s+"
    r"(?:(?:all|any|the|your|my|of|this|that|these|those|previous|prior|earlier|above|original|preceding|current|other|given|user's)\s+)*"
    rf"{TARGET}\b"
    rf"|\b(?:do\s+not|don't|never)\s+(?:follow|obey|complete|do)\s+(?:(?:the|your|any|this|that|user's)\s+)*{TARGET}\b"
    r"|\b(?:without|never|do\s+not|don't)\s+"
    r"(?:ask|asking|tell|telling|inform|informing|notify|notifying|check|checking|confirm|confirming|consult|consulting)\s+(?:with\s+)?"
    r"(?:anyone|anybody|someone|the\s+user|your\s+user|the\s+customer|the\s+owner|the\s+person|a\s+human|a\s+person|them|for\s+(?:approval|permission|confirmation))\b"
    r"|\bwithout\s+(?:approval|permission|confirmation|review)\b"
    r"|\b(?:new|real|updated|actual|true)\s+instructions?\b"
    rf"|{JA_TARGET}(?:内容|フロー|プロセス|ステップ|手続き)?(?:は|を|も)?\s*(?:すべて|全て|全部|一切|いったん|一旦)?\s*{JA_SET_ASIDE}"
    rf"|{JA_TARGET}(?:に|には)\s*(?:従わ|したがわ)(?:ず|ない)"
    r"|(?:承認|許可|確認|同意|了承|レビュー)を?(?:得|取ら|取|求め|もらわ|待た|経)?(?:ずに|ないで|なしで|なしに|なく|せずに|せず)"
    r"|(?:誰|だれ|ユーザー|ユーザ|利用者|お客様|依頼者|担当者|人間|管理者|オーナー)(?:に|にも)\s*(?:何も)?\s*"
    r"(?:聞か|尋ね|たずね|伝え|知らせ|確認せ|相談せ|報告せ|言わ|断ら)(?:ずに|ないで|ず)"
    r"|(?:新しい|新たな|本当の|真の|最新の|更新された|実際の)\s*(?:指示|命令|指令|タスク)"
)
QUOTES = str.maketrans({"‘": "'", "’": "'", "ʼ": "'"})
MARKUP = re.compile(r"<!--.*?(?:-->|\Z)|<[^>]*>", re.DOTALL)
COMMENT_OPEN, COMMENT_CLOSE = "<!--", "-->"
ATTRIBUTE_VALUE = re.compile(r"""[^\s"'<>/=]+\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))""")


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKC", unescape(text))
    return "".join(letter for letter in folded if unicodedata.category(letter) != "Cf").translate(QUOTES).lower()


def gap(first: tuple[int, int], second: tuple[int, int]) -> int:
    return max(second[0] - first[1], first[0] - second[1], 0)


def instruction_passages(passages: Sequence[str]) -> frozenset[int]:
    pieces = [plain(passage) for passage in passages]
    starts: list[int] = []
    at = 0
    for piece in pieces:
        starts.append(at)
        at += len(piece) + 1
    joined = "\n".join(pieces)
    addresses = [found.span() for found in ADDRESS.finditer(joined)]
    if not addresses:
        return frozenset()
    overrides = [found.span() for found in OVERRIDE.finditer(joined)]
    spans = [(min(address[0], override[0]), max(address[1], override[1])) for address in addresses for override in overrides if gap(address, override) <= WINDOW]
    return frozenset(
        index
        for index, (start, piece) in enumerate(zip(starts, pieces, strict=True))
        if piece and any(start < end and begin < start + len(piece) for begin, end in spans)
    )


def has_instruction(texts: Iterable[str]) -> bool:
    return bool(instruction_passages(list(texts)))


def markup_parts(html: str) -> tuple[list[str], list[int]]:
    parts: list[str] = []
    readable: list[int] = []

    def add(text: str, read: bool) -> None:
        if read:
            readable.append(len(parts))
        parts.append(text)

    at = 0
    for found in MARKUP.finditer(html):
        add(html[at : found.start()], True)
        token = found.group()
        if token.startswith(COMMENT_OPEN):
            body_end = len(token) - len(COMMENT_CLOSE) if token.endswith(COMMENT_CLOSE) and len(token) >= 7 else len(token)
            add(COMMENT_OPEN, False)
            add(token[len(COMMENT_OPEN) : body_end], True)
            add(token[body_end:], False)
        else:
            cut = 0
            for attribute in ATTRIBUTE_VALUE.finditer(token):
                group = next(index for index in (1, 2, 3) if attribute.group(index) is not None)
                add(token[cut : attribute.start(group)], False)
                add(attribute.group(group), True)
                cut = attribute.end(group)
            add(token[cut:], False)
        at = found.end()
    add(html[at:], True)
    return parts, readable


def without_instructions(html: str) -> tuple[str, bool]:
    parts, readable = markup_parts(html)
    flagged = instruction_passages([parts[index] for index in readable])
    if not flagged:
        return html, False
    for position in flagged:
        parts[readable[position]] = ""
    return "".join(parts), True
