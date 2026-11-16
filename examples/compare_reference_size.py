"""对比同一参考信息的精简／完整输出；度量字符数，不推算模型 token。"""

import argparse
import json
from pathlib import Path

from marx_lexicon.service import Lexicon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("build/lexicon.sqlite"))
    args = parser.parse_args()
    examples = Path(__file__).resolve().parent
    rows = []
    with Lexicon(args.database) as lexicon:
        for name, edition in (("economics", "edition-capital-de-mew23"),
                              ("philosophy", "edition-manuscripts-de-mew")):
            source = (examples / f"{name}.de.txt").read_text(encoding="utf-8")
            for repeats in (1, 20):
                text = source * repeats
                params = dict(source_language="de", target_language="en",
                              profile_id="demo-de-en", edition_id=edition)
                counts = {}
                for detail in ("compact", "full"):
                    result = lexicon.prepare_translation(text, detail=detail, **params)
                    counts[detail] = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
                rows.append({"example": name, "repeats": repeats, "characters": counts,
                             "reduction_percent": round(100 * (1 - counts["compact"] / counts["full"]), 1)})
        print(json.dumps({"dataset_version": lexicon.dataset.manifest.dataset_version,
                          "metric": "JSON字符数；两种输出均去除格式化空白。不同模型的实际 token 数需按其 tokenizer 测量。",
                          "results": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
