from collections.abc import Callable

from .gemini import generate_post
from .run_store import RunStore
from .threads_client import PublicationUnknown, ThreadsClient, ThreadsError


def post_once(
    prompt: str,
    pro_client: object,
    flash_client: object,
    threads_client: ThreadsClient,
    store: RunStore,
    generator: Callable[[object, object, str], str] = generate_post,
) -> tuple[str, str, str]:
    with store.exclusive():
        store.ensure_publishable()
        run_id = store.start()
        try:
            text = generator(pro_client, flash_client, prompt)
            store.update(run_id, "prepared", text=text)
            container_id = threads_client.create_container(text)
            store.update(run_id, "container_created", container_id=container_id)
            threads_client.wait_until_ready(container_id)
            # 公開要求より先に永続化する。ここ以降の中断は結果不明として扱う。
            store.update(run_id, "publishing")
            post_id = threads_client.publish(container_id)
            store.update(run_id, "published", post_id=post_id)
            return run_id, text, post_id
        except PublicationUnknown:
            store.update(run_id, "unknown")
            raise
        except ThreadsError:
            if store.get(run_id)["status"] == "publishing":
                store.update(run_id, "unknown")
            else:
                store.update(run_id, "failed")
            raise
        except Exception:
            if store.get(run_id)["status"] != "publishing":
                store.update(run_id, "failed")
            raise

