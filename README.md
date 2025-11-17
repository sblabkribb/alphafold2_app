# Alphafold2 RunPod 가이드 (한국어 요약)

이 리포지토리는 RunPod Serverless / Pod 환경에서 Alphafold2 를 실행하기 위한 컨테이너, 스크립트, 샘플 클라이언트를 제공합니다. 아래 내용을 순서대로 따라 하면 됩니다.

---

## 1. 사전 준비

1. **환경 변수(.env)**  
   ```
   RUNPOD_ENDPOINT_ID=...
   RUNPOD_API_KEY=...
   REGISTRY=...
   IMAGE_NAME=alphafold-serverless
   ```
   필요 시 `set -a && source .env && set +a` 로 로드합니다.

2. **이미지 빌드/배포**
   ```
   DOCKER_CLI=docker bash scripts/build_and_push.sh
   ```

3. **Serverless 환경 변수 권장값**
   ```
   ALLOW_DB_AUTO_DOWNLOAD=0
   DB_AUTO_PRESET=full_dbs
   RUNPOD_VOLUME_ROOT=/runpod-volume
   ALPHAFOLD_DB_PATH=/runpod-volume/alphafold
   ALPHAFOLD_MODELS_DIR=/runpod-volume/alphafold/models
   RUNPOD_HANDLER=handler.handler
   RETURN_ARCHIVE=1
   LOG_LEVEL=DEBUG
   ```
   > **DB 파일 경로를 명시해야 하는 경우**
   > 기본적으로 `run_alphafold.sh`가 모든 경로를 자동으로 찾지만, 디렉터리 구조가 달라 hhsearch/bfd 관련 오류가 뜬다면 아래 환경 변수로 정확한 파일/프리픽스를 지정해 주세요.
> ```
> PDB70_DATABASE_PATH=/runpod-volume/alphafold/pdb70/pdb70
> BFD_DATABASE_PATH=/runpod-volume/alphafold/bfd/bfd_metaclust_clu_complete_id30_c90_final_seq.sorted_opt
> UNIREF30_DATABASE_PATH=/runpod-volume/alphafold/uniref30/UniRef30_2023_02
> UNIPROT_DATABASE_PATH=/runpod-volume/alphafold/uniprot/uniprot.fasta
> PDB_SEQRES_DATABASE_PATH=/runpod-volume/alphafold/pdb_seqres/pdb_seqres.txt
> ```
> 멀티머 모드가 아니라면 `UNIPROT/PDB_SEQRES`는 생략해도 됩니다.  
> `reduced_dbs` 프리셋을 사용할 때만 `SMALL_BFD_DATABASE_PATH=/runpod-volume/alphafold/small_bfd/bfd-first_non_consensus_sequences.fasta` 를 지정하고, `full_dbs`에서는 **설정하지 마세요.**

4. **Pod 모드 환경 변수** (네트워크 스토리지를 `/workspace` 로 마운트했다는 가정)
   ```
   RUN_MODE=pod
   RUNPOD_VOLUME_ROOT=/workspace
   RUNPOD_DATA_DIR=/workspace/alphafold
   ALPHAFOLD_DB_PATH=/workspace/alphafold
   ALPHAFOLD_DIR=/workspace/alphafold_src
   ```
   Pod에 접속한 뒤 `export` 해주면 serverless 와 같은 볼륨을 공유합니다.

---

## 2. 데이터 준비

필수 디렉터리 (full_dbs 기준)

| 프리셋 | 필요한 DB/경로 |
| --- | --- |
| 모노머 | `bfd`, `uniref90`, `mgnify`, `pdb70`, `pdb_mmcif`, `uniref30` |
| 멀티머 | `bfd`, `uniref90`, `mgnify`, `uniprot`, `pdb_seqres`, `pdb_mmcif`, `uniref30` |

다운로드 팁

```
cd /workspace/alphafold_src
bash scripts/download_all_data.sh /workspace/alphafold full_dbs
```
중간에 막히면 필요한 항목만 지정 (예: `download_uniref90.sh`) 하거나 수동으로 아카이브를 받아서 풀어도 됩니다.

---

## 3. 로컬/Pod 실행

### 모노머 테스트
```
export MODEL_PRESET=monomer
export ALPHAFOLD_DB_PATH=/workspace/alphafold
export ALPHAFOLD_DIR=/workspace/alphafold_src
/app/run_alphafold.sh /app/sample_data/sequence.fasta /workspace/af_out_mono
```

### 멀티머 테스트
`sample_data/multimer_sample.fasta` (chainA/chainB 두 서열)를 이용합니다.
```
export MODEL_PRESET=multimer
/app/run_alphafold.sh /app/sample_data/multimer_sample.fasta /workspace/af_out_multi
```

실행 로그를 자세히 보고 싶다면
```
bash -x /app/run_alphafold.sh ... 2>&1 | tee /workspace/af_out/run.log
```
로 살펴볼 수 있습니다.

> **참고**  
> - 모노머 preset에서는 `pdb70` 경로만 사용하고 `pdb_seqres` 는 지정하면 안 됩니다.  
> - 멀티머 preset에서는 `pdb_seqres` 와 `uniprot` 이 필수이며 `pdb70` 은 사용하지 않습니다.  
> - `runtime/run_alphafold.sh` 가 preset에 따라 자동으로 올바른 경로를 넘기도록 수정되어 있습니다.

---

## 4. Serverless 요청 방법

모든 DB가 `/runpod-volume/alphafold` 에 준비되어 있다는 가정 하에 아래 명령으로 요청합니다.

```bash
# 모노머
python client/submit_job.py \
  --sequence-file sample_data/sequence.fasta \
  --model-preset monomer \
  --db-preset full_dbs \
  # --insecure (인증서 검증 비활성화, 필요 시) \

# 멀티머
python client/submit_job.py \
  --sequence-file sample_data/multimer_sample.fasta \
  --model-preset multimer \
  --db-preset full_dbs \
  # --insecure (인증서 검증 비활성화, 필요 시) \
```

`RETURN_ARCHIVE=1` 로 설정했다면 작업 완료 시 압축 파일(base64)도 함께 반환됩니다.

---

## 5. 자주 발생하는 문제 해결

| 증상 | 원인 / 해결책 |
| --- | --- |
| `pdb_seqres must not be set ... monomer` | 모노머 preset인데 `pdb_seqres` 를 넘겼을 때 발생. 환경변수 비우거나 최신 `run_alphafold.sh` 사용. |
| `pdb70 must not be set ... multimer` | 멀티머 preset인데 `pdb70` 경로가 포함됨. `MODEL_PRESET=multimer` 로 실행하면 자동으로 처리. |
| `HHBlits database ... not found` | `bfd_metaclust...` 디렉터리 구조 불일치. 압축 해제 후 디렉터리를 올바른 이름으로 맞추거나 `ln -s`. |
| `Jackhmmer` 가 오래 걸린다 | 정상. CPU 단계로 수 분~수십 분 소요될 수 있음. 로그가 진행되는지만 확인. |
| GPU 사용률이 0% | 아직 MSA 단계. Jackhmmer/HHblits 이후 모델 추론이 시작되면 GPU가 사용됨. |

---

## 6. 기타

- `run_alphafold.sh` 는 preset에 맞춰 자동으로 DB 플래그를 구성합니다. 선택형 DB(`small_bfd` 등)는 없으면 건너뛰도록 처리되어 있습니다.
- `sample_data` 폴더에는 모노머/멀티머 예시 FASTA가 포함되어 있어 로컬 테스트에 바로 사용할 수 있습니다.
- Serverless 로그는 RunPod 대시보드 → Endpoints → Jobs 에서 확인하거나 `python client/submit_job.py --status <job-id>` 로 받아볼 수 있습니다.

필요 시 이 README 를 참고해 모노머/멀티머 워크플로를 구성하십시오.
