import os
import torch
import folder_paths
from transformers import AutoTokenizer, AutoModel
from torchvision.transforms.v2 import ToPILImage
import cv2
from PIL import Image


class MiniCPM_VQA:
    def __init__(self):
        self.model_checkpoint = None
        self.tokenizer = None
        self.model = None
        self.device = (
            torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        )
        self.bf16_support = (
            torch.cuda.is_available()
            and torch.cuda.get_device_capability(self.device)[0] >= 8
        )

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": True}),
                "model": (
                    ["MiniCPM-V-2_6-int4", "MiniCPM-Llama3-V-2_5-int4"],
                    {"default": "MiniCPM-V-2_6-int4"},
                ),
                "keep_model_loaded": ("BOOLEAN", {"default": False}),
                "top_p": (
                    "FLOAT",
                    {
                        "default": 0.8,
                    },
                ),
                "top_k": (
                    "INT",
                    {
                        "default": 100,
                    },
                ),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0, "max": 1, "step": 0.1},
                ),
                "repetition_penalty": (
                    "FLOAT",
                    {
                        "default": 1.05,
                    },
                ),
                "max_new_tokens": (
                    "INT",
                    {
                        "default": 2048,
                    },
                ),
                "video_max_num_frames": (
                    "INT",
                    {
                        "default": 64,
                    },
                ),  # if cuda OOM set a smaller number
                "video_max_slice_nums": (
                    "INT",
                    {
                        "default": 2,
                    },
                ),  # use 1 if cuda OOM and video resolution >  448*448
                "seed": ("INT", {"default": -1}),  # add seed parameter, default is -1
            },
            "optional": {
                "source_video_path": ("PATH",),
                "source_image_path_1st": ("IMAGE",),
                "source_image_path_2nd": ("IMAGE",),
                "source_image_path_3rd": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "inference"
    CATEGORY = "Comfyui_MiniCPM-V-2_6-int4"

    def encode_video(self, source_video_path, MAX_NUM_FRAMES):
        def uniform_sample(l, n):  # noqa: E741
            gap = len(l) / n
            idxs = [int(i * gap + gap / 2) for i in range(n)]
            return [l[i] for i in idxs]

        cap = cv2.VideoCapture(source_video_path)
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        print("Total frames:", total_frames)
        avg_fps = cap.get(cv2.CAP_PROP_FPS)
        print("Get average FPS(frame per second):", avg_fps)
        sample_fps = round(avg_fps / 1)  # FPS
        duration = total_frames / avg_fps
        print("Total duration:", duration, "seconds")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print("Video resolution(width x height):", width, "x", height)

        frame_idx = [i for i in range(0, total_frames, sample_fps)]
        if len(frame_idx) > MAX_NUM_FRAMES:
            frame_idx = uniform_sample(frame_idx, MAX_NUM_FRAMES)
        
        frames = []
        for idx in frame_idx:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                # Convert BGR to RGB
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(Image.fromarray(frame))
        
        cap.release()
        print("num frames:", len(frames))
        return frames

    def inference(
        self,
        text,
        model,
        keep_model_loaded,
        top_p,
        top_k,
        temperature,
        repetition_penalty,
        max_new_tokens,
        video_max_num_frames,
        video_max_slice_nums,
        seed,
        source_image_path_1st=None,
        source_image_path_2nd=None,
        source_image_path_3rd=None,
        source_video_path=None,
    ):
        if seed != -1:
            torch.manual_seed(seed)
        model_id = f"openbmb/{model}"
        self.model_checkpoint = os.path.join(
            folder_paths.models_dir, "prompt_generator", os.path.basename(model_id)
        )

        if not os.path.exists(self.model_checkpoint):
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=model_id,
                local_dir=self.model_checkpoint,
                local_dir_use_symlinks=False,
            )

        if self.tokenizer is None:
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_checkpoint,
                trust_remote_code=True,
                low_cpu_mem_usage=True,
            )
        if self.model is None:
            self.model = AutoModel.from_pretrained(
                self.model_checkpoint,
                trust_remote_code=True,
                low_cpu_mem_usage=True,
                attn_implementation="sdpa",
                torch_dtype=torch.bfloat16 if self.bf16_support else torch.float16,
            )

        with torch.no_grad():
            if source_video_path:
                frames = self.encode_video(source_video_path, video_max_num_frames)
                msgs = [{"role": "user", "content": frames + [text]}]
            elif (
                source_image_path_1st is not None
                and source_image_path_2nd is not None
                and source_image_path_3rd is not None
            ):
                image1 = ToPILImage()(
                    source_image_path_1st.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                image2 = ToPILImage()(
                    source_image_path_2nd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                image3 = ToPILImage()(
                    source_image_path_3rd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image1, image2, image3, text]}]
            elif (
                source_image_path_1st is not None
                and source_image_path_2nd is not None
                and source_image_path_3rd is None
            ):
                image1 = ToPILImage()(
                    source_image_path_1st.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                image2 = ToPILImage()(
                    source_image_path_2nd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image1, image2, text]}]
            elif (
                source_image_path_1st is not None
                and source_image_path_2nd is None
                and source_image_path_3rd is not None
            ):
                image1 = ToPILImage()(
                    source_image_path_1st.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                image3 = ToPILImage()(
                    source_image_path_3rd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image1, image3, text]}]
            elif (
                source_image_path_1st is None
                and source_image_path_2nd is not None
                and source_image_path_3rd is not None
            ):
                image2 = ToPILImage()(
                    source_image_path_2nd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                image3 = ToPILImage()(
                    source_image_path_3rd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image2, image3, text]}]
            elif (
                source_image_path_1st is not None
                and source_image_path_2nd is None
                and source_image_path_3rd is None
            ):
                image = ToPILImage()(
                    source_image_path_1st.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image, text]}]
            elif (
                source_image_path_1st is None
                and source_image_path_2nd is not None
                and source_image_path_3rd is None
            ):
                image = ToPILImage()(
                    source_image_path_2nd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image, text]}]
            elif (
                source_image_path_1st is None
                and source_image_path_2nd is None
                and source_image_path_3rd is not None
            ):
                image = ToPILImage()(
                    source_image_path_3rd.permute([0, 3, 1, 2])[0]
                ).convert("RGB")
                msgs = [{"role": "user", "content": [image, text]}]
            else:
                msgs = [{"role": "user", "content": [text]}]
                # raise ValueError("Either image or video must be provided")

            params = {"use_image_id": False, "max_slice_nums": video_max_slice_nums}

            # offload model to CPU
            # self.model = self.model.to(torch.device("cpu"))
            # self.model.eval()

            result = self.model.chat(
                image=None,
                msgs=msgs,
                tokenizer=self.tokenizer,
                sampling=True,
                top_k=top_k,
                top_p=top_p,
                temperature=temperature,
                repetition_penalty=repetition_penalty,
                max_new_tokens=max_new_tokens,
                **params,
            )

            # offload model to GPU
            # self.model = self.model.to(torch.device("cpu"))
            # self.model.eval()

            if not keep_model_loaded:
                del self.tokenizer  # release tokenizer memory
                del self.model  # release model memory
                self.tokenizer = None  # set tokenizer to None
                self.model = None  # set model to None
                torch.cuda.empty_cache()  # release GPU memory
                torch.cuda.ipc_collect()

            return (result,)
