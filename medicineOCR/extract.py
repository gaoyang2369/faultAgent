import cv2
import os
import json
from ultralytics import YOLO

class SignatureExtractor:
    def __init__(self, model_path):#需配置yolo模型路径
        # 初始化只加载一次模型
        self.model = YOLO(model_path)

    def merge_boxes(self, boxes, threshold):
        if not boxes: return []
        nodes = [{"box": b, "merged": False} for b in boxes]
        final_boxes = []
        for i in range(len(nodes)):
            if nodes[i]["merged"]: continue
            current_box = list(nodes[i]["box"])
            nodes[i]["merged"] = True
            changed = True
            while changed:
                changed = False
                for j in range(len(nodes)):
                    if nodes[j]["merged"]: continue
                    target_box = nodes[j]["box"]
                    dx = max(0, current_box[0] - target_box[2], target_box[0] - current_box[2])
                    dy = max(0, current_box[1] - target_box[3], target_box[1] - current_box[3])
                    if dx < threshold and dy < threshold:
                        current_box[0] = min(current_box[0], target_box[0])
                        current_box[1] = min(current_box[1], target_box[1])
                        current_box[2] = max(current_box[2], target_box[2])
                        current_box[3] = max(current_box[3], target_box[3])
                        nodes[j]["merged"] = True
                        changed = True
            final_boxes.append(current_box)
        return final_boxes

    def process(self, img_path, output_dir, merge_threshold=80, padding=15):#需配置图片输入路径
        # 建立输出目录
        crops_dir = os.path.join(output_dir, "crops")
        os.makedirs(crops_dir, exist_ok=True)
        
        img = cv2.imread(img_path)
        if img is None: raise Exception(f"Failed to load image: {img_path}")
        h, w = img.shape[:2]
        clean_img = img.copy()
        img_name = os.path.basename(img_path)

        results = self.model.predict(img, conf=0.2)
        raw_boxes = [list(map(int, box.xyxy[0].cpu().numpy())) for box in results[0].boxes]
        merged_boxes = self.merge_boxes(raw_boxes, merge_threshold)

        layout_data = {
            "source_file": img_name,
            "canvas_size": {"width": w, "height": h},
            "signatures": []
        }

        for i, box in enumerate(merged_boxes):
            x1, y1, x2, y2 = box
            c_x1, c_y1 = max(0, x1 - padding), max(0, y1 - padding)
            c_x2, c_y2 = min(w, x2 + padding), min(h, y2 + padding)
            
            crop_img = img[c_y1:c_y2, c_x1:c_x2]
            crop_filename = f"sig_{i}_{img_name}"
            crop_path = os.path.join(crops_dir, crop_filename)
            cv2.imwrite(crop_path, crop_img)

            layout_data["signatures"].append({
                "id": i,
                "crop_file": crop_filename,
                "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1
            })
            # 涂白原图中的签名
            cv2.rectangle(clean_img, (x1-2, y1-2), (x2+2, y2+2), (255, 255, 255), -1)

        cleaned_path = os.path.join(output_dir, f"cleaned_{img_name}")
        cv2.imwrite(cleaned_path, clean_img)
        
        json_path = os.path.join(output_dir, "layout_metadata.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(layout_data, f, indent=4, ensure_ascii=False)

        return cleaned_path, json_path, crops_dir