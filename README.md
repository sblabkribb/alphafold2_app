# Alphafold2 RunPod Serverless

Quick Run — Sample Request
- Ensure your endpoint is deployed with this image and your network volume is populated (reduced or full DBs). If not, trigger preload first; see “Service-triggered DB Preload (Async)”.
- Linux/macOS:
```
export RUNPOD_ENDPOINT_ID=<your-endpoint-id>
export RUNPOD_API_KEY=<your-api-key>
python3 client/submit_job.py --sequence-file sample_data/sequence.fasta --db-preset full_dbs
```
- Windows PowerShell:
```
$env:RUNPOD_ENDPOINT_ID='<your-endpoint-id>'
$env:RUNPOD_API_KEY='<your-api-key>'
python client\submit_job.py --sequence-file sample_data\sequence.fasta --db-preset full_dbs
```
- If your network inspects TLS (corporate proxy), add `--verify <CA.pem>` or use `--insecure` (development only). See “Research Network TLS/CA” below.

RunPod Serverless 배포를 위한 Alphafold2 이미지 및 사용 도구 모음입니다. 전체 설계와 추후 TODO는 `docs/runpod_serverless_spec.md`에서 확인할 수 있습니다.

## 구성 요소
- `docker/Docker.base` – Alphafold2 의존성을 담은 베이스 이미지
- `docker/Docker` – 런타임 핸들러가 포함된 최종 이미지
- `scripts/build_and_push.sh` – 이미지 빌드 및 레지스트리 푸시 자동화
- `runtime/handler.py` – RunPod Serverless 핸들러
- `runtime/run_alphafold.sh` – Alphafold 실행 래퍼
- `client/submit_job.py` – RunPod API 요청 예제
- `client/control.py` – 엔드포인트에 비동기 제어 액션 전송(프리로드/상태/중지)
- `sample_data/sequence.fasta` – 테스트 FASTA 예제

## 환경변수 주입
.env.example을 .env로 복사하여 안에 값 채우고 난 뒤

```bash
Alphafold2 on Runpod Serverless — Build, Bootstrap, and Client Usage

Quick start
- Export env: `set -a && source .env && set +a`
- Build and push: `DOCKER_CLI=docker bash scripts/build_and_push.sh`
  - If `IMAGE_TAG` is empty, a timestamp tag is used automatically and `:latest` is also created and pushed. Disable with `ALSO_LATEST=0`.
- Optional corporate CA during build: `CORP_CA_PATH=certs/soosan-eprism.crt DOCKER_CLI=docker bash scripts/build_and_push.sh`

Serverless environment variables
- `ALPHAFOLD_DB_PATH=/data/alphafold` (default)
- `RUNPOD_VOLUME_ROOT=/runpod-volume` (mount your Runpod volume here)
- `ALLOW_DB_AUTO_DOWNLOAD=1` to auto‑download DBs if missing
- `DB_AUTO_PRESET=reduced_dbs` for small setup; avoid `full_dbs` on cold start

What bootstrap does
- Links `/data/alphafold` → `/runpod-volume/alphafold` when volume is present.
- Downloads model parameters into `/data/alphafold/models` if missing.
- DB population when missing:
  - If `DB_SYNC_CMD` is set, runs it (e.g., rclone/gsutil/aws s3 sync).
  - Else if `ALLOW_DB_AUTO_DOWNLOAD=1`, runs `/app/download_db.sh ${ALPHAFOLD_DB_PATH} ${DB_AUTO_PRESET}`.
- Logs df/ls/du at startup so you can confirm sizes.

Recommended storage
- reduced_dbs: 300–400 GB recommended (operates around 200–300 GB).
- full_dbs: multi‑TB; pre‑load on an On‑Demand instance into the same volume.

Preload on On‑Demand (reduced_dbs)
```
VOL=${RUNPOD_VOLUME_ROOT:-/runpod-volume}; [ -d "$VOL" ] || VOL=/workspace
mkdir -p "$VOL/alphafold"; export ALPHAFOLD_DB_PATH="$VOL/alphafold"
bash /app/download_models.sh "$ALPHAFOLD_DB_PATH"
bash /app/download_db.sh "$ALPHAFOLD_DB_PATH" reduced_dbs
du -sh "$ALPHAFOLD_DB_PATH"/*
```

### Service-triggered DB Preload (Async)
- 서버리스로 “다운로드만” 트리거하고 싶다면 핸들러에 `action`을 전달하세요.
- 핸들러가 백그라운드로 `/app/bootstrap_db.sh`를 실행하고 즉시 응답합니다. 진행 로그는 `<ALPHAFOLD_DB_PATH>/bootstrap_<preset>.log`에 기록됩니다.

예시
- 풀 DB 비동기 프리로드 시작:
```
python3 client/control.py preload --preset full_dbs
```
- 상태 조회(디렉터리 존재/용량, 실행 중 프로세스):
```
python3 client/control.py status
```
- 진행 중 작업 중지:
```
python3 client/control.py stop
```
비고
- 중복 실행 방지를 위해 내부적으로 `flock`을 사용해 한 번에 하나만 실행됩니다.
- tar 추출 충돌을 피하기 위해 기본적으로 `TAR_OPTIONS="--no-same-owner --skip-old-files"`가 적용됩니다.

### Windows PowerShell Quick Start
- 환경변수 설정 후, 보안 검증을 임시로 끄고 실행(개발용):
```
$env:RUNPOD_ENDPOINT_ID='n3tcpxdv3irr46'
$env:RUNPOD_API_KEY='rpa_...'
python client\control.py preload --preset full_dbs --insecure
```
- 상태 확인(동일하게 검증 끄기):
```
python client\control.py status --insecure
```
- 사내 CA를 사용할 경우(권장):
```
python client\control.py preload --preset full_dbs --verify 'C:\\Users\\user\\Documents\\GitHub\\alphafold2_app\\certs\\soosan-eprism.crt'
python client\control.py status --verify 'C:\\Users\\user\\Documents\\GitHub\\alphafold2_app\\certs\\soosan-eprism.crt'
```

### Research Network TLS/CA
- 배경: 연구소/회사 네트워크는 SSL/TLS 검사를 위해 프록시가 자체 CA로 재서명한 인증서를 내보냅니다. 컨테이너/파이썬이 이 CA를 모르면 요청이 실패합니다.
- 컨테이너 내부
  - 이 레포의 `certs/*.crt`/`*.pem`는 이미지 빌드 시 시스템 신뢰 저장소에 설치됩니다.
  - Python은 기본적으로 시스템 번들(`REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt`)을 사용합니다.
- 로컬(Windows) 클라이언트에서 호출할 때 옵션
  - CA 지정: `--verify C:\path\to\corp-ca.crt` 또는 `$env:REQUESTS_CA_BUNDLE='C:\path\to\corp-ca.crt'`
  - CA+certifi 결합 번들로 안정화: 위 “Windows PowerShell Quick Start”의 combined.pem 예시 참고
  - 임시 개발용: `--insecure` (권장하지 않음)

### Troubleshooting (Ops)
- params 파일이 계속 쌓임: 스크립트 특성상 매 실행 params tar(5.2GB)를 다시 받습니다. 장시간 작업은 한 번만 실행하고, 멈췄을 때 `find "$DATA/params" -maxdepth 1 -name 'alphafold_params_2022-12-06*.tar' -delete`로 정리.
- rsync 필요: `rsync --version` 확인. 이미지에 포함됨.
- 서버리스 프리로드가 중단됨: 워커 수명 때문에 중단될 수 있습니다. On‑Demand Pod에서 동일 볼륨에 선로딩 권장.
- 중복 다운로드 정리: `pgrep -fa 'download_all_data.sh|aria2c|rsync'` → PPID=1 고아 aria2c는 종료.

### Pod 모드 환경 변수
- RunPod Pod에서 이 이미지를 사용할 때는 `RUN_MODE=pod`를 함께 지정해 handler가 serverless 워커를 띄우지 않고 대기 상태로 유지되도록 합니다.
- 권장 환경 변수
  ```
  RUN_MODE=pod
  RUNPOD_VOLUME_ROOT=/workspace
  RUNPOD_DATA_DIR=/workspace/alphafold
  ALPHAFOLD_DB_PATH=/workspace/alphafold
  ALPHAFOLD_DIR=/workspace/alphafold_src
  ```
- Pod에 SSH 접속한 뒤 위 값을 `export` 하면 `/workspace` 네트워크 볼륨을 serverless와 동일하게 공유할 수 있습니다.

### 모델 프리셋별 주의사항
- `MODEL_PRESET=monomer` : `pdb70`, `bfd`, `uniref90`, `mgnify`, `uniref30` 등이 필요하며 `pdb_seqres` 는 전달하면 안 됩니다. (스크립트가 자동으로 `pdb70` 만 넘깁니다.)
- `MODEL_PRESET=multimer` : `pdb_seqres`, `uniprot`, `bfd`, `uniref90`, `mgnify`, `uniref30` 이 필요하고 `pdb70` 은 사용하지 않습니다.
- 실행 예
  ```bash
  # Monomer
  export MODEL_PRESET=monomer
  /app/run_alphafold.sh /app/sample_data/sequence.fasta /workspace/af_out

  # Multimer
  export MODEL_PRESET=multimer
  /app/run_alphafold.sh /app/sample_data/sequence.fasta /workspace/af_out_multimer
  ```
- 로컬 테스트용 FASTA
  - 모노머: `/app/sample_data/sequence.fasta`
  - 멀티머: `/app/sample_data/multimer_sample.fasta` (chainA/chainB 두 서열 포함)
  - 예시 실행
    ```bash
    # 멀티머 테스트 (Pod/온디맨드)
    export MODEL_PRESET=multimer
    /app/run_alphafold.sh /app/sample_data/multimer_sample.fasta /workspace/af_out_multimer
    ```

## Image notes
- Adjusted SciPy(1.8.1) and pandas(1.3.5) pins for Python 3.10 compatibility.
- Stereo chemical properties file fetched from multiple mirrors for robustness.
- Rsync included: required by AlphaFold `download_all_data.sh` for UniRef90/PDB mmCIF. Without it, downloads stall at params.
- Corporate CA handling:
  - Put your corporate CA in `certs/` (e.g., `certs/soosan-eprism.crt`); baked into the image trust store at build.
  - Requests uses system trust via `REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt`.
  - Optionally inject CA at build with `CORP_CA_PATH=... ./scripts/build_and_push.sh`.
  - Security: bake corporate CAs only in private images/registries.


## 빠른 시작
1. 레지스트리 정보를 환경 변수로 설정한 뒤 빌드/푸시
   ```bash
   export REGISTRY=ghcr.io/my-org
   export IMAGE_NAME=alphafold-serverless
   export IMAGE_TAG=dev
   ./scripts/build_and_push.sh
   ```
2. RunPod에서 Serverless 엔드포인트 생성 (`RUNPOD_HANDLER=handler.handler`)
3. 샘플 요청 전송
   ```bash
   python3 client/submit_job.py --endpoint n3tcpxdv3irr46 --api-key 'rpa_xxx' --sequence-file sample_data/sequence.fasta --ca-bundle certs/soosan-eprism.crt
   ```
   또는 .env 로드 후 동기 실행(폴링 포함)
   ```bash
   set -a && source .env && set +a
   REQUESTS_CA_BUNDLE=certs/soosan-eprism.crt python3 client/submit_job.py --sequence-file sample_data/sequence.fasta
   ```

추가 세부사항 및 테스트 절차는 명세서를 참고하세요.
