#!/usr/bin/env python3
"""校验 ugcli 打包产物（upk）是否完整可用。

检查项：
  1. 容器结构：magic + sig/pub/ico/ugb/obj 段长度正确、无错位（坏包最典型的特征）
  2. 载荷结构：ugb -> tar.gz -> 内层 .ugb -> xz -> tar 能正常解开
  3. 包内自洽：.check-app 记录的每个文件 MD5 与实际内容一致
  4. 运行时一致性：execStart 指向的文件存在且可执行，端口与 proxy 目标一致
  5. 与 product.json 对账：hash / size / version / arch / productSeries

用法：
  python3 tools/verify_upk.py                         # 校验 build_dir/pkgs/upk 下所有 upk
  python3 tools/verify_upk.py build_dir/pkgs/upk/a.upk [b.upk ...]
  python3 tools/verify_upk.py --no-color build_dir/pkgs/upk/a.upk

退出码：全部通过 0，任一失败 1。
"""

import argparse
import gzip
import hashlib
import io
import json
import lzma
import re
import sys
import tarfile
from pathlib import Path

MAGIC = b"UGREEN-PKG-FORMAT"
EXPECTED_SEGMENTS = ["sig", "pub", "ico", "ugb", "obj"]


def parse_container(data):
    """按 magic + key:len:payload 解析容器，返回 (段字典, 问题列表)。"""
    problems = []
    if not data.startswith(MAGIC):
        return {}, ["文件头不是 UGREEN-PKG-FORMAT，不是 upk 或已损坏"]

    segments = {}
    pos = len(MAGIC)
    while pos < len(data):
        m = re.match(rb"([a-z0-9_]+):(\d+):", data[pos:pos + 48])
        if not m:
            problems.append(
                f"容器结构错位：偏移 {pos} 处无法解析段头（后续 {len(data) - pos} 字节被污染）"
            )
            break
        key, size = m.group(1).decode(), int(m.group(2))
        start = pos + m.end()
        end = start + size
        if end > len(data):
            problems.append(f"段 {key} 声明长度 {size}，但文件已到末尾（截断）")
            break
        segments[key] = data[start:end]
        pos = end

    missing = [k for k in EXPECTED_SEGMENTS if k not in segments]
    if missing:
        problems.append(f"缺少段：{', '.join(missing)}")
    order = [k for k in segments if k in EXPECTED_SEGMENTS]
    if order != [k for k in EXPECTED_SEGMENTS if k in order]:
        problems.append(f"段顺序异常：{order}")
    if len(segments.get("sig", b"")) not in (0, 256):
        problems.append(f"sig 段长度异常：{len(segments['sig'])}（应为 256）")
    if "ico" in segments and not segments["ico"].startswith(b"\x89PNG"):
        problems.append("ico 段不是 PNG")
    obj = segments.get("obj")
    if obj is not None:
        if len(obj) % 2 or not re.fullmatch(rb"[0-9a-fA-F]+", obj or b""):
            problems.append("obj 段不是合法的十六进制清单，包体可能被改写")
    return segments, problems


def extract_payload(segments):
    """ugb -> tar.gz -> 内层 .ugb，返回内层 tar 的字节流。"""
    outer = tarfile.open(fileobj=io.BytesIO(gzip.decompress(segments["ugb"])))
    members = [m for m in outer.getmembers() if m.name.endswith(".ugb")]
    if not members:
        raise RuntimeError("ugb 内没有 .ugb 载荷文件")
    return outer.extractfile(members[0]).read()


def open_inner(raw):
    for opener in (lzma.decompress, gzip.decompress):
        try:
            return tarfile.open(fileobj=io.BytesIO(opener(raw)))
        except Exception:
            continue
    return tarfile.open(fileobj=io.BytesIO(raw))


def check_package(upk_path, color=True):
    """校验单个 upk，返回 (是否通过, 输出行列表)。"""
    lines = []
    problems = []
    upk = Path(upk_path)
    data = upk.read_bytes()
    md5 = hashlib.md5(data).hexdigest()

    lines.append(f"检查 {upk.name}")
    lines.append(f"  大小      : {len(data)} 字节")
    lines.append(f"  MD5       : {md5}")

    segments, seg_problems = parse_container(data)
    problems += seg_problems
    if segments:
        desc = " ".join(f"{k}({len(v)})" for k, v in segments.items())
        lines.append(f"  容器      : {desc}")

    inner_tar = None
    config = None
    if not seg_problems and "ugb" in segments:
        try:
            inner_tar = open_inner(extract_payload(segments))
            names = [m.name.lstrip("./") for m in inner_tar.getmembers()]
            lines.append(f"  载荷      : {len(names)} 个条目")
            try:
                config = json.loads(inner_tar.extractfile("./config.json").read())
                version = config.get("version", {}).get("version", "?")
                lines.append(
                    f"  目标      : arch={config.get('arch')} series={config.get('productSeries')} version={version}"
                )
            except Exception as exc:
                problems.append(f"包内 config.json 无法解析：{exc}")
        except Exception as exc:
            problems.append(f"载荷无法解开：{exc}")

    # 包内自洽：.check-app 的 MD5 清单
    if inner_tar is not None:
        try:
            stored = json.loads(inner_tar.extractfile("./.check-app").read())
            bad = []
            for name, expect in stored.items():
                got = hashlib.md5(inner_tar.extractfile("./" + name).read()).hexdigest()
                if got != expect:
                    bad.append(name)
            lines.append(f"  包内校验  : {len(stored) - len(bad)}/{len(stored)} 通过")
            if bad:
                problems.append(f".check-app MD5 不匹配：{', '.join(bad)}")
        except Exception as exc:
            problems.append(f".check-app 无法解析：{exc}")

    # 运行时一致性：execStart 可执行、端口与 proxy 一致
    if config and inner_tar is not None:
        runtime = config.get("runtimeEnvironment", {})
        exec_start = runtime.get("service", {}).get("execStart", "")
        target = exec_start.split()[0] if exec_start else ""
        members = {m.name.lstrip("./"): m for m in inner_tar.getmembers()}
        if not target:
            problems.append("config.json 缺少 execStart")
        elif target not in members:
            problems.append(f"execStart 指向的 {target} 不在包内")
        elif not members[target].mode & 0o111:
            problems.append(f"execStart 指向的 {target} 没有可执行位（mode={oct(members[target].mode)}）")
        else:
            lines.append(f"  execStart : {exec_start} ✓")

        port = config.get("checkParameter", {}).get("portChecker", {}).get("portFlag")
        for proxy in runtime.get("proxy", []) or []:
            if str(proxy.get("target")) != str(port):
                problems.append(f"proxy 目标 {proxy.get('target')} 与探测端口 {port} 不一致")

    # 与 product.json 对账
    product_json = upk.with_name(upk.name[: -len(".upk")] + ".product.json")
    if product_json.exists():
        try:
            meta = json.loads(product_json.read_text())
            pairs = [
                ("hash", meta.get("hash"), md5),
                ("size", meta.get("version", {}).get("size"), len(data)),
                ("version", meta.get("version", {}).get("version"), (config or {}).get("version", {}).get("version")),
                ("arch", meta.get("arch"), (config or {}).get("arch")),
                ("series", meta.get("productSeries"), (config or {}).get("productSeries")),
            ]
            diffs = [f"{k}({got}!={want})" for k, got, want in pairs if want is not None and got != want]
            if diffs:
                problems.append(f"product.json 对账不一致：{', '.join(diffs)}")
            else:
                lines.append("  product   : hash/size/version/arch/series 对账 ✓")
        except Exception as exc:
            problems.append(f"product.json 无法解析：{exc}")
    else:
        lines.append("  product   : 未找到同名 product.json，跳过对账")
        lines.append("  警告      : 缺少 product.json 时无法发现字节级损坏，建议打包产物连同 product.json 一起保留")

    lines.append(f"  结果      : {'PASS' if not problems else 'FAIL'}")
    return not problems, lines, problems


def main():
    parser = argparse.ArgumentParser(description="校验 ugcli 打包产物（upk）是否完整可用")
    parser.add_argument("paths", nargs="*", help="upk 文件，默认校验 build_dir/pkgs/upk 下全部")
    parser.add_argument("--no-color", action="store_true", help="不使用颜色")
    args = parser.parse_args()

    use_color = sys.stdout.isatty() and not args.no_color
    green = "\033[92m" if use_color else ""
    red = "\033[91m" if use_color else ""
    reset = "\033[0m" if use_color else ""

    if args.paths:
        targets = [Path(p) for p in args.paths]
    else:
        default_dir = Path(__file__).resolve().parent.parent / "build_dir" / "pkgs" / "upk"
        targets = sorted(default_dir.glob("*.upk"))
        if not targets:
            print(f"没有找到 upk：{default_dir}")
            return 1

    failed = 0
    for idx, upk in enumerate(targets):
        if not upk.exists():
            print(f"{red}找不到文件：{upk}{reset}")
            failed += 1
            continue
        if idx:
            print()
        ok, lines, problems = check_package(upk)
        for line in lines:
            if line.strip().startswith("结果"):
                print(f"{green if ok else red}{line}{reset}")
            else:
                print(line)
        for problem in problems:
            print(f"{red}        ✗ {problem}{reset}")
        if not ok:
            failed += 1

    total = len(targets)
    print()
    print(f"{green if not failed else red}{total - failed}/{total} 个包通过校验{reset}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
