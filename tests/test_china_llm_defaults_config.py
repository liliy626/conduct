import inspect
import yaml


def test_china_embedding_defaults_are_loaded_from_model_config() -> None:
    from gateway_core.infra import china_llm_defaults as defaults

    config = yaml.safe_load(open("model_config.yaml", encoding="utf-8"))
    china_defaults = config["embedding_defaults"]["china"]
    assert (
        defaults.DEFAULT_CHINA_EMBED_MODEL,
        defaults.DEFAULT_CHINA_EMBED_API_KEY_ENV,
        defaults.DEFAULT_CHINA_EMBED_BASE_URL,
        defaults.DEFAULT_CHINA_EMBED_DIM,
    ) == (china_defaults["model"], china_defaults["api_key_env"], china_defaults["base_url"], china_defaults["dimensions"])
    assert "DEFAULT_CHINA_EMBED_MODEL = \"" not in inspect.getsource(defaults)
