# RunPod Serverless Alphafold2 이미지 / 배포 명세

## 1. 목표
- Alphafold2 추론을 RunPod Serverless 환경에서 수행할 수 있는 이미지를 제공한다.
- 기본 환경 의존성과 프로젝트 코드를 분리하여 **`Docker.base`** → **`Docker`** 2단계 빌드 체인을 구성한다.
- `build_and_push.sh` 스크립트로 베이스/최종 이미지를 순차적으로 빌드하고 컨테이너 레지스트리(ECR/GCR/Docker Hub 등)로 푸시한다.
- 로컬에서 요청 생성을 검증할 수 있는 간단한 Python 클라이언트를 제공한다.

## 2. 최종 산출물
```
alphafold2_app/
├─ docs/
│  └─ runpod_serverless_spec.md      # (본 문서)
├─ docker/
│  ├─ Docker.base                    # 베이스 이미지 (의존성, alphafold 설치)
│  └─ Docker                         # 런타임 이미지 (핸들러/엔트리포인트)
├─ scripts/
│  └─ build_and_push.sh              # 빌드 및 푸시 자동화
├─ runtime/
│  ├─ handler.py                     # RunPod serverless 핸들러
│  ├─ download_models.sh             # 모델 가중치 프리패칭 (옵션)
│  └─ run_alphafold.sh               # Alphafold 추론 래퍼
└─ client/
   └─ submit_job.py                  # RunPod API 요청 예제
```

## 3. Docker 구성

### 3.1 Docker.base
- **베이스 이미지**: `nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04`
- **시스템 패키지**
  - `build-essential`, `wget`, `git`, `curl`, `python3.10`, `python3-pip`, `python3-venv`, `openjdk-11-jdk`, `aria2`, `libssl-dev`, `libffi-dev`, `libxml2`, `libxmlsec1-dev`, `libopenmm-dev`, `libcudnn8`
  - Alphafold 전처리에 필요한 `hmmer`, `kalign`, `jackhmmer`, `hhsuite`
- **Python 환경**
  - `/opt/alphafold` 경로에 alphafold 소스 클론 (`release-2.3.2` 태그)
  - `pip install -r requirements.txt`
  - OpenMM, Kalign 추가 파이썬 의존성 설치
  - Alphafold 실행 스크립트를 PATH에 추가
- **모델 데이터**
  - 기본 이미지는 모델 가중치를 포함하지 않는다.
  - 대신 `/opt/alphafold/run_alphafold.sh`에서 런타임에 RunPod Volume 또는 S3에서 마운트하도록 설계.
- **캐시 최적화**
  - apt/pip 캐시는 빌드 마지막에 정리.

### 3.2 Docker
- **베이스**: `Docker.base`에서 빌드된 이미지 사용 (`ARG BASE_IMAGE`)
- **구성 요소**
  - `runtime/handler.py`, `runtime/run_alphafold.sh` 등을 `/app`에 복사
  - 필요한 `ENTRYPOINT`/`CMD` 설정 (RunPod Serverless는 `handler` 노출)
  - `ENV PYTHONUNBUFFERED=1`, `ENV RUNPOD_HANDLER=handler.handler`
- **헬스 체크**
  - 간단한 self-test 스크립트를 추가할 수 있도록 `CMD ["python3", "-m", "handler"]` 구조 고려

### 3.3 런타임 스크립트
- `run_alphafold.sh`: 입력 FASTA 파일 경로와 출력 디렉터리를 받아 Alphafold 메인 스크립트를 실행한다.
- `handler.py`: RunPod serverless 형식에 맞춰 `def handler(event):` 구현.
  - 입력: FASTA 시퀀스 문자열 또는 FASTA 파일 URL
  - 처리: 임시 디렉터리에 FASTA 작성 → `run_alphafold.sh` 실행 → 결과 디렉터리를 압축 후 업로드/반환
  - 출력: 결과 파일(예: PDB) 경로 또는 presigned URL
- `download_models.sh` (옵션): 이미지 빌드 후 최초 실행 시 모델 가중치를 미리 내려받는 유틸리티.

## 4. 빌드 & 배포 프로세스

1. `.env` 또는 환경 변수로 레지스트리 정보 제공
   - `REGISTRY=registry.hub.docker.com/username`
   - `IMAGE_NAME=alphafold-serverless`
   - `IMAGE_TAG=latest` (기본은 `$(date +%Y%m%d)` 형태 권장)
2. `scripts/build_and_push.sh`
   - 베이스 이미지 태그(`$REGISTRY/$IMAGE_NAME-base:$IMAGE_TAG`) 정의
   - 최종 이미지 태그(`$REGISTRY/$IMAGE_NAME:$IMAGE_TAG`) 정의
   - `docker build -f docker/Docker.base -t ...`
   - `docker build -f docker/Docker --build-arg BASE_IMAGE=... -t ...`
   - `docker push` 두 이미지
   - `set -euo pipefail` 적용, 로깅 강화
3. (선택) `download_models.sh`를 실행하여 미리 모델 다운로드, RunPod Volume에 저장.

## 5. RunPod Serverless 배포 & 요청 흐름

1. RunPod 대시보드에서 Serverless 엔드포인트 생성
   - 위에서 푸시한 이미지 지정
   - 핸들러 이름: `handler.handler`
   - 환경 변수: 모델 경로, 캐시 경로, S3 자격 정보 등
   - GPU 타입 및 메모리 설정 (예: `Nvidia A10G`, 24GB RAM)
2. 요청 예제 (`client/submit_job.py`)
   - `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY` 환경 변수 사용
   - API 호출: `POST https://api.runpod.ai/v2/{ENDPOINT_ID}/run`
   - payload: FASTA sequence, optional inference params (`max_template_date`, `model_preset`)
   - 비동기 응답 처리: job status polling (`/status/{taskId}`)
   - 결과 다운로드: presigned URL 또는 base64 결과 저장

## 6. 테스트 전략

### 6.1 정적 검사
- `shellcheck scripts/build_and_push.sh`
- `python -m compileall runtime client`

### 6.2 로컬 시뮬레이션
- Docker 이미지 빌드 시 `RUN ["python3", "/app/handler.py", "--self-test"]` 같은 경량 테스트 수행
- 로컬 Docker 컨테이너로 `handler` 호출 테스트:
  ```
  docker run --rm \
    -e RUNPOD_TEST_MODE=1 \
    -v $(pwd)/sample_data:/data \
    alphafold-serverless:latest \
    python3 /app/handler.py --local sample_data/input.fasta
  ```

### 6.3 RunPod 연동
- `client/submit_job.py` 실행 전 `RUNPOD_API_KEY` 확인
- 샘플 FASTA (`sample_data/sequence.fasta`)로 테스트 요청 전송
- 실행 후 응답 시간/결과 파일 검증

## 7. 단계별 작업 로드맵

1. **문서 & 골격 작성**
   - 현재 문서 정리
   - 디렉터리 생성 (`docker/`, `scripts/`, `runtime/`, `client/`, `sample_data/`)
2. **Docker.base 구현**
   - CUDA 베이스 선택, 의존성 설치
   - alphafold 소스 / 파이썬 환경 준비
3. **Docker & 런타임 핸들러 구현**
   - 핸들러, shell 스크립트 작성
   - 도커 build 시 기본 자체 테스트 수행
4. **빌드 스크립트 및 클라이언트 스크립트 작성**
   - `build_and_push.sh`, `client/submit_job.py`
   - 샘플 데이터/ENV 예시 추가
5. **테스트 & 점검**
   - `shellcheck`, `python -m compileall`
   - 로컬 handler self-test
6. **최종 검토**
   - 문서 업데이트
   - 차후 TODO (모델 다운로드 자동화 등) 정리

## 8. 향후 개선 사항
- 모델 가중치 자동 캐싱/동기화 (S3, RunPod Volume)
- FastAPI 기반 REST wrapper 제공
- 멀티 시퀀스 배치 처리
- 관측값(메트릭) -> 로깅/모니터링 시스템 연동

