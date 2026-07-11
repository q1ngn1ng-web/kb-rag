"""LLM 连接验证脚本。

用法:
    export DEEPSEEK_API_KEY="sk-..."
    python scripts/verify_llm.py

    # 或使用 ollama 本地模型:
    python scripts/verify_llm.py --provider ollama --base-url http://localhost:11434/v1
"""

import argparse
import os
import sys

# 将 src 加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="验证 LLM 连接")
    parser.add_argument("--provider", default="", choices=["xiaomi", "deepseek", "ollama", ""])
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    from knowledge_base.config import load_config

    config = load_config()
    llm_config = config["llm"]

    if args.base_url:
        llm_config["base_url"] = args.base_url
    if args.model:
        llm_config["model"] = args.model
    if args.provider:
        llm_config["provider"] = args.provider

    print(f"Provider: {llm_config['provider']}")
    print(f"Base URL: {llm_config['base_url']}")
    print(f"Model:    {llm_config['model']}")
    print(f"API Key:  {'***' + llm_config['api_key'][-4:] if llm_config['api_key'] else '(空)'}")
    print()

    if not llm_config["api_key"]:
        print("错误: 未设置 API Key。请设置 DEEPSEEK_API_KEY 环境变量。")
        sys.exit(1)

    import openai

    client = openai.OpenAI(
        api_key=llm_config["api_key"],
        base_url=llm_config["base_url"],
    )

    try:
        print("正在测试 LLM 连接...")
        response = client.chat.completions.create(
            model=llm_config["model"],
            messages=[{"role": "user", "content": "你好，请用一句话介绍自己。"}],
            max_tokens=100,
            temperature=0.7,
        )
        reply = response.choices[0].message.content
        print(f"✓ LLM 连接成功!")
        print(f"回复: {reply}")
    except Exception as e:
        print(f"✗ LLM 连接失败: {e}")
        sys.exit(1)

    # 测试 embedding（使用独立的 embedding 配置，不走 LLM 客户端）
    embed_config = config.get("embedding", {})
    embed_api_key = embed_config.get("api_key") or os.environ.get("DASHSCOPE_API_KEY", "")
    embed_base_url = embed_config.get("base_url", "")
    embed_model = embed_config.get("model", "")

    if embed_api_key and embed_base_url and embed_model:
        print(f"\nEmbedding 配置:")
        print(f"  Base URL: {embed_base_url}")
        print(f"  Model:    {embed_model}")
        print(f"  API Key:  {'***' + embed_api_key[-4:] if embed_api_key else '(空)'}")
        try:
            embed_client = openai.OpenAI(
                api_key=embed_api_key,
                base_url=embed_base_url,
            )
            print("\n正在测试 Embedding...")
            response = embed_client.embeddings.create(
                model=embed_model,
                input="测试文本",
            )
            dims = len(response.data[0].embedding)
            print(f"✓ Embedding 成功! 向量维度: {dims}")
        except Exception as e:
            print(f"✗ Embedding 连接失败: {e}")
    else:
        print(f"\n✗ Embedding 未配置 (需设置 embedding.base_url / api_key / model)")


if __name__ == "__main__":
    main()
