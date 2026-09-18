"""Explicit, fail-closed experimental interventions for stock PaddleOCR-VL v0.29.0.

Changes model input metadata/batching, not weights. Validate output consistency and CUDA
traces before adopting. These are diagnostic options, disabled in normal chart values.
"""
from pathlib import Path
import sys

mode=sys.argv[1]
assert mode in ['host-sequence-lengths','packed-vision'],mode
path=Path('/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/paddleocr_vl.py')
source=path.read_text()
old='max_seqlen = (cu_seqlens[1:] - cu_seqlens[:-1]).max()'
assert source.count(old)==1,'Unexpected model source; refusing to patch'
# The grids originate on the host and encode the same sequence boundaries as cu_seqlens.
# A CPU scalar retains the wrapper interface while .item() no longer synchronizes CUDA.
source=source.replace(old,"max_seqlen = torch.tensor(max(t * h * w for t, h, w in flatten_image_grid_thw), dtype=torch.int32, device='cpu')")
if mode=='packed-vision':
    old='''        vision_outputs = tuple(
            self.encode_image(pixel, grid).squeeze(0)
            for pixel, grid in zip(pixel_values, image_grid_thw)
        )'''
    assert source.count(old)==1,'Unexpected image loop; refusing to patch'
    new='''        # Pack independent images into one variable-length attention execution.
        # cu_seqlens prevents attention across image boundaries; split before projection.
        grids = [tuple(grid.tolist()) for grid in image_grid_thw]
        sizes = [int(t * h * w) for t, h, w in grids]
        pixels = torch.cat([pixel.type(self.visual.dtype) for pixel in pixel_values], dim=0)
        bounds = [0]
        for size in sizes:
            bounds.append(bounds[-1] + size)
        positions = torch.cat([
            torch.arange(size) % (h * w)
            for size, (t, h, w) in zip(sizes, grids)
        ]).to(pixels.device, non_blocking=True)
        bounds_gpu = async_tensor_h2d(bounds, dtype=torch.int32, device=pixels.device)
        packed = self.visual(pixel_values=pixels, image_grid_thw=grids,
            position_ids=positions, interpolate_pos_encoding=True, cu_seqlens=bounds_gpu)
        vision_outputs = tuple(packed.squeeze(0).split(sizes, dim=0))'''
    source=source.replace(old,new)
compile(source,str(path),'exec')
path.write_text(source)
print('Applied benchmark VLM intervention:',mode,flush=True)
