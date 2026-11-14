# defect-vision
from ultralytics import YOLO
model = YOLO('defects.pt')
import onnxruntime as ort
