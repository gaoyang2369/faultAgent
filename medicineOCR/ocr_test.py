import torch
from modelscope import AutoModel, AutoTokenizer

class DeepSeekOCR:
    def __init__(self, model_path):
        print("Loading DeepSeek-OCR-2...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)#需配置OCR模型路径
        self.model = AutoModel.from_pretrained(
            model_path, 
            trust_remote_code=True, 
            use_safetensors=True
        ).eval().cuda().to(torch.bfloat16)

    def infer(self, image_path):
        prompt = "<image>\n<|grounding|>Convert the document to markdown."
        with torch.no_grad():
            res = self.model.infer(
                self.tokenizer, 
                prompt=prompt, 
                image_file=image_path,
                base_size=1024,
                image_size=768,
                crop_mode=True,
                save_results=False
            )
        return res 