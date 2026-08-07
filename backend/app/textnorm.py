"""Нормализация названий: кириллица, латиница и узбекская орфография — к одной форме.

`Куйлюк`, `Qo'yliq`, `Kuyluk`, `қуйлиқ` после нормализации дают одну строку (ТЗ р. 10).
Порядок шагов важен и повторяет ТЗ: транслитерация → регистр → диграфы → буквы.
"""

from __future__ import annotations

# кириллица → латиница по узбекской схеме
CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h", "ә": "a", "ө": "o",
}
# варианты апострофа, которыми в узбекской латинице пишут o‘ и g‘
APOSTROPHES = "'‘’ʻʼ´`"
# диграфы сворачиваются, чтобы sh/s и ch/c не расходились
DIGRAPHS = (("o'", "o"), ("g'", "g"), ("sh", "s"), ("ch", "c"), ("ng", "n"))
# буквы, которые в разных вариантах записи взаимозаменяемы
LETTERS = (("x", "h"), ("q", "k"), ("y", "i"))


def transliterate(text: str) -> str:
    return "".join(CYRILLIC_TO_LATIN.get(ch, CYRILLIC_TO_LATIN.get(ch.lower(), ch)) for ch in text)


def normalize(text: str | None) -> str:
    if not text:
        return ""
    out = transliterate(text.lower()).lower()
    for ap in APOSTROPHES:
        out = out.replace(ap, "'")
    for src, dst in DIGRAPHS:
        out = out.replace(src, dst)
    out = out.replace("'", "")
    for src, dst in LETTERS:
        out = out.replace(src, dst)
    return "".join(ch for ch in out if ch.isalnum() or ch == " ").strip()


def levenshtein(a: str, b: str, limit: int) -> int:
    """Расстояние Левенштейна с ранним выходом: больше limit нас не интересует."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[-1])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]
