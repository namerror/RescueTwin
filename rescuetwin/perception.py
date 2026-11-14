"""Optional real-image detector, intentionally independent of simulator entity IDs."""
from dataclasses import dataclass
import math

@dataclass
class Detection:
    box:list[float]
    confidence:float
    position:list[float]|None

class PersonDetector:
    def __init__(self,weights='yolo26n.pt',device='cpu'):
        from ultralytics import YOLO
        self.model=YOLO(weights);self.device=device

    def detect(self,image,depth=None,intrinsics=None):
        """Depth is metres along camera Z; returned position is in camera coordinates."""
        result=self.model.predict(image,classes=[0],conf=.4,device=self.device,verbose=False)[0]
        detections=[]
        for box in result.boxes:
            bounds=box.xyxy[0].tolist();position=None
            if depth is not None and intrinsics is not None:
                x1,y1,x2,y2=map(int,bounds)
                values=[float(depth[y][x]) for y in range(max(0,y1),min(len(depth),y2))
                        for x in range(max(0,x1),min(len(depth[0]),x2)) if math.isfinite(float(depth[y][x])) and depth[y][x]>0]
                if values:
                    values.sort();z=values[len(values)//2];u=(bounds[0]+bounds[2])/2;v=(bounds[1]+bounds[3])/2
                    fx,fy,cx,cy=intrinsics;position=[(u-cx)*z/fx,(v-cy)*z/fy,z]
            detections.append(Detection(bounds,float(box.conf[0]),position))
        return detections
