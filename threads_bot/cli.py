import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import (
    ConfigurationError,
    gemini_credentials,
    load_environment,
    threads_credentials,
)
from .gemini import GenerationError, create_clients, generate_post
from .run_store import RunStore, RunStoreError
from .service import post_once
from .threads_client import PublicationUnknown, ThreadsClient, ThreadsError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Geminiで投稿本文を作成し、Threadsへ1件投稿します。")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("generate", "post"):
        command = commands.add_parser(name)
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument("--prompt", help="投稿の指示文")
        group.add_argument("--prompt-file", type=Path, help="UTF-8の投稿指示文ファイル")
        if name == "post":
            command.add_argument("--dry-run", action="store_true", help="生成だけ行い、投稿しない")
    status = commands.add_parser("status", help="投稿実行の状態を表示")
    status.add_argument("--run-id", help="省略時は最新の実行")
    resolve = commands.add_parser("resolve", help="結果不明の投稿をThreads上で照合した後に確定")
    resolve.add_argument("--run-id", required=True)
    outcome = resolve.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--confirmed-not-published", action="store_true")
    outcome.add_argument("--post-id", help="公開済みのThreads投稿ID")
    return root


def _prompt(args: argparse.Namespace) -> str:
    if args.prompt_file is not None:
        try:
            value = args.prompt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ConfigurationError("投稿プロンプトのファイルを読み込めません。") from exc
    else:
        value = args.prompt
    if not value or not value.strip():
        raise ConfigurationError("投稿プロンプトが空です。")
    return value.strip()


def _generate(args: argparse.Namespace, publish: bool) -> int:
    prompt = _prompt(args)
    gemini = gemini_credentials()
    threads = threads_credentials() if publish else None
    pro_client, flash_client = create_clients(gemini)
    try:
        if not publish:
            print(generate_post(pro_client, flash_client, prompt))
            return 0
        store = RunStore()
        api = ThreadsClient(threads)
        try:
            run_id, text, post_id = post_once(prompt, pro_client, flash_client, api, store)
            print(text)
            print(f"実行ID: {run_id}\n投稿ID: {post_id}", file=sys.stderr)
            return 0
        finally:
            api.close()
            store.close()
    finally:
        pro_client.close()
        flash_client.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_environment()
    try:
        if args.command == "generate":
            return _generate(args, publish=False)
        if args.command == "post":
            return _generate(args, publish=not args.dry_run)
        store = RunStore()
        try:
            if args.command == "status":
                row = store.get(args.run_id)
                if row is None:
                    raise RunStoreError("実行記録がありません。")
                print(json.dumps(row, ensure_ascii=False, indent=2))
                return 0
            with store.exclusive():
                if args.confirmed_not_published:
                    store.resolve(args.run_id, "failed")
                else:
                    store.resolve(args.run_id, "published", post_id=args.post_id)
            print(f"実行 {args.run_id} の状態を更新しました。", file=sys.stderr)
            return 0
        finally:
            store.close()
    except ConfigurationError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except GenerationError as exc:
        print(f"生成エラー: {exc}", file=sys.stderr)
        return 3
    except PublicationUnknown as exc:
        print(f"公開結果不明: {exc}", file=sys.stderr)
        return 5
    except ThreadsError as exc:
        print(f"Threadsエラー: {exc}", file=sys.stderr)
        return 4
    except (RunStoreError, OSError) as exc:
        print(f"実行記録エラー: {exc}", file=sys.stderr)
        return 1

