# Alphafold2 RunPod Serverless

RunPod Serverless 배포를 위한 Alphafold2 이미지 및 사용 도구 모음입니다. 전체 설계와 추후 TODO는 `docs/runpod_serverless_spec.md`에서 확인할 수 있습니다.

## 구성 요소
- `docker/Docker.base` – Alphafold2 의존성을 담은 베이스 이미지
- `docker/Docker` – 런타임 핸들러가 포함된 최종 이미지
- `scripts/build_and_push.sh` – 이미지 빌드 및 레지스트리 푸시 자동화
- `runtime/handler.py` – RunPod Serverless 핸들러
- `runtime/run_alphafold.sh` – Alphafold 실행 래퍼
- `client/submit_job.py` – RunPod API 요청 예제
- `sample_data/sequence.fasta` – 테스트 FASTA 예제

## 환경변수 주입
.env.example을 .env로 복사하여 안에 값 채우고 난 뒤

```bash
set -a
source .env
set +a
```


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

