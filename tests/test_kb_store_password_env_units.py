"""向量库口令从环境变量取(容器化需要,2026-09-21)。

`kb.yaml` 在**共享** NAS 上(`/nas/kb/kb.yaml`),全体用户同一份;而加密凭据
`$GSDB_HOME/credentials/*.enc` 是**按用户**的(`/nas/me/gdaa`)。要让每个 Pod 都能连向量库,
按老路子就得给每个用户各塞一份同样的凭据——多一个人就多一份要轮换的口令副本。

现成的 `GSDB_PASSWORD` 能绕过去,但它是**全局**覆盖:对任何凭据名都返回同一个值。
一旦设了它,数据库连接口令和向量库口令就是同一个,而且谁都看不出来。所以不用它。

改成按 store 指定环境变量名,与 `EmbeddingConfig.api_key_env` 同一个路子:
    store.pg.password_env: KB_PG_PASSWORD
没配 password_env 时行为完全不变(仍走加密凭据),非容器部署不受影响。
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import config as kbconfig  # noqa: E402
from common.kb import query as kbquery    # noqa: E402

_YAML = """\
store:
  pg: {host: vectordb, port: 5432, database: kb, user: kb, credential: kb-pg%s}
embeddings: {source: url, base_url: http://embed:11434/v1, model: bge-m3, dims: 1024}
"""


def _cfg(tmp_path, extra=""):
    kb = tmp_path / "kb"
    (kb / "cases").mkdir(parents=True)
    (kb / "kb.yaml").write_text(_YAML % extra, encoding="utf-8")
    return kbconfig.load(kb)


def test_password_env_is_parsed(tmp_path):
    assert _cfg(tmp_path, ", password_env: KB_PG_PASSWORD").store.pg.password_env == "KB_PG_PASSWORD"


def test_absent_password_env_defaults_to_empty(tmp_path):
    assert _cfg(tmp_path).store.pg.password_env == ""


def test_lookup_prefers_the_named_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_PG_PASSWORD", "from-secret")
    cfg = _cfg(tmp_path, ", password_env: KB_PG_PASSWORD")
    got = kbquery.store_password(cfg.store.pg, lambda name: "from-encrypted-file")
    assert got == "from-secret"


def test_lookup_falls_back_to_the_credential_file(tmp_path, monkeypatch):
    """没配 password_env:行为跟加这个特性之前一模一样。"""
    monkeypatch.delenv("KB_PG_PASSWORD", raising=False)
    cfg = _cfg(tmp_path)
    assert kbquery.store_password(cfg.store.pg, lambda name: "from-encrypted-file") == "from-encrypted-file"


def test_named_env_set_but_empty_is_an_error(tmp_path, monkeypatch):
    """**核心**:平台把 Secret 挂漏了,环境变量存在但是空串。

    这时候绝不能悄悄退回加密凭据——那份凭据在容器里根本不存在,报出来的会是
    「没有 kb-pg 的凭据」,把人往「凭据没存」的方向带,而真正的毛病是 Secret 没挂上。
    """
    monkeypatch.setenv("KB_PG_PASSWORD", "")
    cfg = _cfg(tmp_path, ", password_env: KB_PG_PASSWORD")
    with pytest.raises(kbconfig.KbConfigError) as exc:
        kbquery.store_password(cfg.store.pg, lambda name: "from-encrypted-file")
    assert "KB_PG_PASSWORD" in str(exc.value)


def test_named_env_missing_entirely_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_PG_PASSWORD", raising=False)
    cfg = _cfg(tmp_path, ", password_env: KB_PG_PASSWORD")
    with pytest.raises(kbconfig.KbConfigError):
        kbquery.store_password(cfg.store.pg, lambda name: "from-encrypted-file")


def test_user_env_lets_one_shared_kb_yaml_serve_two_roles(tmp_path, monkeypatch):
    """**为什么需要它**:kb.yaml 在共享 NAS 上只有一份、只有一个 `user:` 字段,
    但 runtime 要用只读库用户、kb-import 要用读写库用户 —— 一个字段装不下两个。

    不解决的话只能给两边同一个读写用户,那 runtime 对 kb/ 的只读挂载就白设了:
    用户让模型读出环境变量里的口令,就能直接改派生索引。
    """
    monkeypatch.setenv("KB_PG_USER", "kb_ro")
    cfg = _cfg(tmp_path, ", user_env: KB_PG_USER")
    assert kbquery.store_user(cfg.store.pg) == "kb_ro"


def test_user_env_absent_falls_back_to_the_yaml_field(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_PG_USER", raising=False)
    assert kbquery.store_user(_cfg(tmp_path).store.pg) == "kb"


def test_user_env_set_but_empty_is_an_error(tmp_path, monkeypatch):
    """跟口令同一个道理:悄悄回落到 yaml 里的用户名,就会用错权限去连库。"""
    monkeypatch.setenv("KB_PG_USER", "")
    cfg = _cfg(tmp_path, ", user_env: KB_PG_USER")
    with pytest.raises(kbconfig.KbConfigError) as exc:
        kbquery.store_user(cfg.store.pg)
    assert "KB_PG_USER" in str(exc.value)


def test_import_side_uses_the_same_password_source(tmp_path, monkeypatch):
    """写侧(kb.py index / store 子命令)与读侧必须同一条取口令的路。

    两侧各写一份的话,容器里会出现「查得通、写不进」——而且写侧报的是
    「取不到凭据 kb-pg」,看起来像凭据没存,跟 Secret 一点关系都没有。
    """
    sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-kb-import" / "scripts"))
    import kb_store

    monkeypatch.setenv("KB_PG_PASSWORD", "from-secret")
    cfg = _cfg(tmp_path, ", password_env: KB_PG_PASSWORD")
    seen = {}
    monkeypatch.setattr(kb_store.spg.PgStore, "connect",
                        staticmethod(lambda *a, **kw: seen.update(pw=a[4]) or object()))
    kb_store.open_pg(cfg)
    assert seen["pw"] == "from-secret"
