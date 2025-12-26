import os
import base64
import requests

# Ollama API.
# - Ollama는 호스트에서 실행한다고 가정한다.
# - Jenkins 컨테이너에서 host.docker.internal:11434 로 접근한다.
# - 응답을 스트리밍하지 않고 한 번에 받으므로 네트워크 타임아웃을 길게 잡는다.
OLLAMA_API = "http://host.docker.internal:11434/api/generate"

# 입력/출력 디렉토리.
# - SOURCE_DIR: 사용자가 올린 원본 이미지 위치
# - RESULT_DIR: Vision 분석 결과를 MD로 저장
SOURCE_DIR = "/var/knowledges/docs/org"
RESULT_DIR = "/var/knowledges/docs/result"


def analyze_image(image_path: str) -> str:
    """
    이미지 파일을 Llama 3.2 Vision 모델로 분석해 설명 텍스트를 얻는다.

    출력은 Markdown 문서에 그대로 넣을 수 있는 자연어 설명을 목표로 한다.
    - 입력 이미지는 base64로 인코딩해 Ollama API의 images 필드로 전달한다.
    - stream=False로 설정해 한 번에 응답을 받고, 'response' 필드를 그대로 반환한다.
    - 오류 시 호출 측(main)이 빈 문자열을 받아 결과 파일 생성을 건너뛰도록 한다.
    """
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "model": "llama3.2-vision:latest",
        "prompt": "Describe this image in detail for markdown documentation.",
        "stream": False,
        "images": [img_b64],
    }

    resp = requests.post(OLLAMA_API, json=payload, timeout=300)
    data = resp.json()
    return data.get("response", "")


def main() -> None:
    """
    SOURCE_DIR에서 지원 확장자 이미지를 순회하며 Vision 분석을 수행하고,
    결과를 RESULT_DIR에 <원본파일명>.md로 저장한다.
    - md 파일 헤더에 원본 파일명을 남겨 추적성을 유지한다.
    - 분석 실패(빈 문자열) 시 해당 이미지는 건너뛴다.
    """
    os.makedirs(RESULT_DIR, exist_ok=True)

    for root, _, files in os.walk(SOURCE_DIR):
        for name in files:
            ext = name.lower().split(".")[-1]
            if ext not in ["jpg", "jpeg", "png", "bmp", "webp"]:
                continue

            full_path = os.path.join(root, name)
            print(f"[Vision] {name}")

            desc = analyze_image(full_path)
            if not desc:
                continue

            out_path = os.path.join(RESULT_DIR, f"{name}.md")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"# {name} (Vision Analysis)\n\n{desc}\n")

            print(f"[Saved] {out_path}")


if __name__ == "__main__":
    main()
