#!/bin/bash

# Fine-tune ViZipVoice (Vietnamese, simple character tokenizer) on a custom
# dataset prepared by scripts/data/prepare_tts_dataset.py.
#
# Mirrors run_finetune.sh but: tokenizer=simple (no espeak), single-GPU
# defaults, and stage 4 pulls the ViZipVoice checkpoint/model.json/tokens.txt
# from HF contextboxai/ViZipvoice (or copies them from a local --model-dir
# style directory passed via model_dir=...).
#
# Inputs: two TSV files `{uniq_id}\t{text}\t{wav_path}` — override via
#   train_tsv=... dev_tsv=... bash run_finetune_vi.sh

# Add project root to PYTHONPATH
export PYTHONPATH=../../:$PYTHONPATH

set -e
set -u
set -o pipefail

stage=${stage:-1}
stop_stage=${stop_stage:-6}

# Number of jobs for data preparation
nj=${nj:-20}

# ViZipVoice uses the simple per-character tokenizer (no espeak/g2p)
tokenizer=simple

# Python interpreter (override with e.g. python=.venv/Scripts/python on Windows)
python=${python:-python3}

# Maximum length (seconds) of the training utterance
max_len=${max_len:-20}

# Mixed precision (set use_fp16=0 to train in fp32)
use_fp16=${use_fp16:-1}

# Download directory for pre-trained models
download_dir=download/

# Local dir that already contains checkpoint-1860000.pt, model.json, tokens.txt.
# When set, stage 4 copies from here instead of downloading from HF.
model_dir=${model_dir:-}

hf_repo=contextboxai/ViZipvoice
checkpoint_name=checkpoint-1860000.pt

# HF CLI: `hf` on huggingface_hub>=1.0, `huggingface-cli` on older versions
hf_cli=${hf_cli:-hf}

# Training hyper-parameters (overridable for smoke runs)
num_iters=${num_iters:-20000}
save_every=${save_every:-1000}
base_lr=${base_lr:-0.00002}
max_duration=${max_duration:-120}
world_size=${world_size:-1}
num_buckets=${num_buckets:-30}
exp_dir=${exp_dir:-exp/zipvoice_finetune_vi}
# Init weights for stage 5 (default: the downloaded ViZipVoice checkpoint).
# Point at an earlier exp checkpoint-N.pt to continue after a crash.
init_checkpoint=${init_checkpoint:-}
num_workers=${num_workers:-8}

# TSV inputs produced by scripts/data/prepare_tts_dataset.py
train_tsv=${train_tsv:-data/raw/custom_train.tsv}
dev_tsv=${dev_tsv:-data/raw/custom_dev.tsv}

for file_path in ${train_tsv} ${dev_tsv}; do
      [ -f "$file_path" ] || { echo "Error: expect $file_path !" >&2; exit 1; }
done

### Prepare the training data (1 - 4)

if [ ${stage} -le 1 ] && [ ${stop_stage} -ge 1 ]; then
      echo "Stage 1: Prepare manifests for custom dataset from tsv files"

      for subset in train dev;do
            if [ "$subset" = "train" ]; then tsv=${train_tsv}; else tsv=${dev_tsv}; fi
            ${python} -m zipvoice.bin.prepare_dataset \
                  --tsv-path ${tsv} \
                  --prefix vizipvoice-finetune \
                  --subset raw_${subset} \
                  --num-jobs ${nj} \
                  --output-dir data/manifests
      done
      # Output: data/manifests/vizipvoice-finetune_cuts_raw_{train,dev}.jsonl.gz
fi

if [ ${stage} -le 2 ] && [ ${stop_stage} -ge 2 ]; then
      echo "Stage 2: Add tokens to manifests"
      for subset in train dev;do
            ${python} -m zipvoice.bin.prepare_tokens \
                  --input-file data/manifests/vizipvoice-finetune_cuts_raw_${subset}.jsonl.gz \
                  --output-file data/manifests/vizipvoice-finetune_cuts_${subset}.jsonl.gz \
                  --num-jobs ${nj} \
                  --tokenizer ${tokenizer}
      done
fi

if [ $stage -le 3 ] && [ $stop_stage -ge 3 ]; then
      echo "Stage 3: Compute Fbank for custom dataset"
      for subset in train dev; do
            ${python} -m zipvoice.bin.compute_fbank \
                  --source-dir data/manifests \
                  --dest-dir data/fbank \
                  --dataset vizipvoice-finetune \
                  --subset ${subset} \
                  --num-jobs ${nj}
      done
fi

if [ ${stage} -le 4 ] && [ ${stop_stage} -ge 4 ]; then
      echo "Stage 4: Fetch ViZipVoice checkpoint, tokens file, and model config"
      mkdir -p ${download_dir}/vizipvoice
      if [ -n "${model_dir}" ]; then
            echo "Using local model dir: ${model_dir}"
            for file in ${checkpoint_name} model.json tokens.txt; do
                  if [ ! -f "${model_dir}/${file}" ] && [ "${file}" = "model.json" ] && [ -f "${model_dir}/config.json" ]; then
                        cp "${model_dir}/config.json" "${download_dir}/vizipvoice/model.json"
                        continue
                  fi
                  cp "${model_dir}/${file}" "${download_dir}/vizipvoice/${file}"
            done
      else
            for file in ${checkpoint_name} model.json tokens.txt; do
                  ${hf_cli} download \
                        --local-dir ${download_dir}/vizipvoice \
                        ${hf_repo} \
                        ${file}
            done
      fi
fi

### Training ZipVoice (5 - 6)

if [ ${stage} -le 5 ] && [ ${stop_stage} -ge 5 ]; then
      echo "Stage 5: Fine-tune the ViZipVoice model"

      [ -z "$max_len" ] && { echo "Error: max_len is not set!" >&2; exit 1; }

      ${python} -m zipvoice.bin.train_zipvoice \
            --world-size ${world_size} \
            --use-fp16 ${use_fp16} \
            --finetune 1 \
            --base-lr ${base_lr} \
            --num-iters ${num_iters} \
            --save-every-n ${save_every} \
            --max-duration ${max_duration} \
            --num-buckets ${num_buckets} \
            --max-len ${max_len} \
            --model-config ${download_dir}/vizipvoice/model.json \
            --checkpoint ${init_checkpoint:-${download_dir}/vizipvoice/${checkpoint_name}} \
            --num-workers ${num_workers} \
            --tokenizer ${tokenizer} \
            --token-file ${download_dir}/vizipvoice/tokens.txt \
            --dataset custom \
            --train-manifest data/fbank/vizipvoice-finetune_cuts_train.jsonl.gz \
            --dev-manifest data/fbank/vizipvoice-finetune_cuts_dev.jsonl.gz \
            --exp-dir ${exp_dir}
fi

if [ ${stage} -le 6 ] && [ ${stop_stage} -ge 6 ]; then
      echo "Stage 6: Average the checkpoints for ZipVoice"
      ${python} -m zipvoice.bin.generate_averaged_model \
            --iter ${num_iters} \
            --avg 2 \
            --model-name zipvoice \
            --exp-dir ${exp_dir}
      # The generated model is ${exp_dir}/iter-${num_iters}-avg-2.pt
fi
