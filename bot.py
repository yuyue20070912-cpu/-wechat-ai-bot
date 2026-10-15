import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def ask_ai(message):
    response = client.responses.create(
        model="gpt-6-luna",
        input=message
    )
    return response.output_text


def main():
    print("AI 机器人启动成功！")
    print("输入 quit 退出。")

    while True:
        message = input("你：")

        if message.lower() == "quit":
            break

        try:
            answer = ask_ai(message)
            print("机器人：", answer)
        except Exception as e:
            print("发生错误：", e)


if __name__ == "__main__":
    main()