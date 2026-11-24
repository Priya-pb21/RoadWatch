import torch
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
from PIL import Image

# Load pretrained model
weights = MobileNet_V3_Small_Weights.DEFAULT
model = mobilenet_v3_small(weights=weights)
model.eval()

# Load image
image = Image.open("images/pothole.jpg").convert("RGB")

# Preprocess image
preprocess = weights.transforms()
input_tensor = preprocess(image).unsqueeze(0)

# Predict
with torch.no_grad():
    output = model(input_tensor)

# Get top 5 predictions
probabilities = torch.nn.functional.softmax(output[0], dim=0)
top5 = torch.topk(probabilities, 5)

categories = weights.meta["categories"]

print("\nImage classification results:")

for index, score in zip(top5.indices, top5.values):
    print(f"{categories[index]}: {score.item():.2%}")