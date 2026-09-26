"""
nst_engine.py

Neural Style Transfer engine — refactored from the original research notebook
(Gatys et al., 2015 — VGG19 feature extraction, Gram-matrix style loss, L-BFGS
pixel optimization) into a reusable, thread-safe module callable from a web
backend.

The optimization logic (layers, losses, gram matrix, L-BFGS loop) is kept
faithful to the notebook. What changed is packaging: the notebook worked on
files inside a mounted dataset folder and plotted results with matplotlib;
this module works on in-memory PIL images, streams progress via a callback,
and returns a PIL image so a web server can serve it back to a client.
"""

import io
import threading

import torch
import torch.optim as optim
from PIL import Image
from torchvision import models, transforms

# ---------------------------------------------------------------------------
# Device + model loading (loaded once, shared across requests)
# ---------------------------------------------------------------------------

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# VGG19 layer index -> human name, same mapping as the notebook
LAYER_MAP = {
    "0": "conv1_1",
    "5": "conv2_1",
    "10": "conv3_1",
    "19": "conv4_1",
    "21": "conv4_2",
    "28": "conv5_1",
}
CONTENT_LAYER = "conv4_2"
STYLE_LAYERS = ["conv1_1", "conv2_1", "conv3_1", "conv4_1", "conv5_1"]

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

_vgg = None
_vgg_lock = threading.Lock()


def get_vgg():
    """Lazily load & freeze VGG19 once per process, then reuse it."""
    global _vgg
    if _vgg is None:
        with _vgg_lock:
            if _vgg is None:
                model = models.vgg19(weights=models.VGG19_Weights.IMAGENET1K_V1)
                model = model.features.to(device).eval()
                for p in model.parameters():
                    p.requires_grad_(False)
                _vgg = model
    return _vgg


# ---------------------------------------------------------------------------
# Image <-> tensor helpers
# ---------------------------------------------------------------------------


def make_loader(img_size: int):
    return transforms.Compose(
        [
            transforms.Resize((img_size, img_size)),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )


def pil_to_tensor(image: Image.Image, img_size: int) -> torch.Tensor:
    loader = make_loader(img_size)
    image = image.convert("RGB")
    tensor = loader(image).unsqueeze(0)
    return tensor.to(device, torch.float)


def tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    image = tensor.detach().cpu().clone().squeeze(0)
    for c, m, s in zip(range(3), IMAGENET_MEAN, IMAGENET_STD):
        image[c] = image[c] * s + m
    image = image.clamp(0, 1)
    return transforms.ToPILImage()(image)


def tensor_to_png_bytes(tensor: torch.Tensor) -> bytes:
    pil_img = tensor_to_pil(tensor)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Feature extraction + losses (identical math to the notebook)
# ---------------------------------------------------------------------------


def get_features(image: torch.Tensor, model, layer_map=LAYER_MAP):
    features = {}
    x = image
    for name, layer in model._modules.items():
        x = layer(x)
        if name in layer_map:
            features[layer_map[name]] = x
    return features


def gram_matrix(tensor: torch.Tensor) -> torch.Tensor:
    b, c, h, w = tensor.size()
    features = tensor.view(c, h * w)
    gram = torch.mm(features, features.t())
    return gram / (c * h * w)


def compute_content_loss(gen_features, content_features):
    diff = gen_features[CONTENT_LAYER] - content_features[CONTENT_LAYER]
    return torch.mean(diff ** 2)


def compute_style_loss(gen_features, style_grams):
    loss = 0.0
    for layer in STYLE_LAYERS:
        gen_gram = gram_matrix(gen_features[layer])
        style_gram = style_grams[layer]
        loss = loss + torch.mean((gen_gram - style_gram) ** 2)
    return loss


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_style_transfer(
    content_image: Image.Image,
    style_image: Image.Image,
    img_size: int = 384,
    steps: int = 200,
    content_weight: float = 1.0,
    style_weight: float = 1e6,
    progress_callback=None,
) -> Image.Image:
    """
    Run Gatys-style Neural Style Transfer.

    progress_callback, if given, is called as progress_callback(step, total_steps,
    content_loss, style_loss) after every L-BFGS closure evaluation, so a caller
    (e.g. a background job) can report live progress to a client.
    """
    vgg = get_vgg()

    content_tensor = pil_to_tensor(content_image, img_size)
    style_tensor = pil_to_tensor(style_image, img_size)

    with torch.no_grad():
        content_features = get_features(content_tensor, vgg)
        style_features = get_features(style_tensor, vgg)
        style_grams = {layer: gram_matrix(style_features[layer]) for layer in STYLE_LAYERS}

    generated = content_tensor.clone().requires_grad_(True)
    optimizer = optim.LBFGS([generated])

    run = [0]

    def closure():
        optimizer.zero_grad()
        gen_features = get_features(generated, vgg)

        c_loss = compute_content_loss(gen_features, content_features)
        s_loss = compute_style_loss(gen_features, style_grams)
        total_loss = content_weight * c_loss + style_weight * s_loss
        total_loss.backward()

        run[0] += 1
        if progress_callback is not None:
            progress_callback(run[0], steps, c_loss.item(), s_loss.item())

        return total_loss

    while run[0] <= steps:
        optimizer.step(closure)

    with torch.no_grad():
        generated.clamp_(-3, 3)  # keep normalized values in a sane range

    return tensor_to_pil(generated)
