"""Opt-in HPS stage instrumentation. Appended to the model before Triton starts.

Times wall and calling-thread CPU, not GPU kernel duration. Overlapping stages must
not be added together. No document text/images are logged. A per-experiment cache
salt prevents prior benchmark inputs from inflating prefix-cache hit rates.
"""
import functools as _functools
import inspect as _inspect
import json as _json
import os as _os
import time as _time

_active_benchmark_batch_id = None


def _emit(stage, start, cpu, **extra):
    record = dict(stage=stage, pid=_os.getpid(), started=start,
                  seconds=_time.time()-start, thread_cpu_seconds=_time.thread_time()-cpu,
                  batch_id=_active_benchmark_batch_id,
                  **extra)
    print("PADDLE_BENCH " + _json.dumps(record), flush=True)


def _timed(fn, stage):
    if _inspect.isgeneratorfunction(fn):
        @_functools.wraps(fn)
        def generator(*args, **kwargs):
            start, cpu = _time.time(), _time.thread_time()
            try:
                yield from fn(*args, **kwargs)
            finally:
                _emit(stage, start, cpu)
        return generator
    @_functools.wraps(fn)
    def call(*args, **kwargs):
        global _active_benchmark_batch_id
        extra = {}
        if stage == "hps.run_batch":
            _active_benchmark_batch_id = args[3] if len(args) > 3 else kwargs.get("batch_id")
            extra["requests"] = len(args[1] if len(args) > 1 else kwargs["inputs"])
        start, cpu = _time.time(), _time.thread_time()
        result = None
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            if stage == "hps._preprocess" and isinstance(result, tuple) and len(result) == 3:
                extra["pages"] = len(result[0])
            _emit(stage, start, cpu, **extra)
            if stage == "hps.run_batch":
                _active_benchmark_batch_id = None
    return call


for _name in ["_preprocess", "_postprocess", "run_batch"]:
    setattr(TritonPythonModel, _name, _timed(getattr(TritonPythonModel, _name), "hps."+_name))

from paddlex.inference.pipelines.paddleocr_vl import pipeline as _pipeline
from paddlex.inference.models.doc_vlm import predictor as _predictor

for _module, _methods in [
    (_pipeline, ["_paddleocr_vl_prepare_page_serial_benchmarked",
                 "_paddleocr_vl_layout_prep_parallel_pages",
                 "_paddleocr_vl_run_vl_recognition_batches",
                 "_paddleocr_vl_assemble_parsing_results"]),
    (_predictor, ["_doc_vlm_genai_build_request_specs", "_doc_vlm_genai_collect_responses"]),
]:
    for _cls in vars(_module).values():
        if not isinstance(_cls,type):
            continue
        for _name in _methods:
            if _name not in vars(_cls):
                continue
            _fn = getattr(_cls,_name)
            if _name == "_doc_vlm_genai_build_request_specs":
                def _salted(*args, _fn=_fn, **kwargs):
                    specs = _fn(*args,**kwargs)
                    # Reread at each batch so an experiment can change salt without restart.
                    try:
                        with open('/tmp/paddle-bench-cache-salt') as f:
                            salt=f.read().strip()
                    except FileNotFoundError:
                        salt=''
                    if salt:
                        nonce=str(_time.time_ns())
                        for index, (messages, request_kwargs) in enumerate(specs):
                            request_kwargs.setdefault('extra_body',{})['cache_salt']=salt
                            # Prefix salts do not invalidate vLLM's separate encoder cache.
                            for message in messages:
                                for part in message.get('content', []):
                                    if part.get('type') == 'image_url':
                                        part['uuid']='%s:%s:%s:%s'%(salt,_os.getpid(),nonce,index)
                    # Explicit marker enables private crop replay capture for diagnosis.
                    # Off for throughput trials; image content is never printed to logs.
                    if _os.path.exists('/tmp/paddle-bench-capture-crops'):
                        _os.makedirs('/tmp/paddle-bench-crops',exist_ok=True)
                        path='/tmp/paddle-bench-crops/%s-%s.json'%(_os.getpid(),_time.time_ns())
                        with open(path,'w') as f:
                            _json.dump({'created':_time.time(),'specs':specs},f)
                    print('PADDLE_BENCH '+_json.dumps({'stage':'crop_group_submitted',
                          'pid':_os.getpid(),'started':_time.time(),'crops':len(specs),
                          'batch_id':_active_benchmark_batch_id}),flush=True)
                    return specs
                _fn = _salted
            setattr(_cls,_name,_timed(_fn,_name))

# Time the layout predictor's generator while preserving all predictor attributes.
_original_initialize = TritonPythonModel.initialize
def _initialize_profiled(self,*args,**kwargs):
    result=_original_initialize(self,*args,**kwargs)
    # PaddleX's public pipeline delegates to an inner implementation.
    candidates=[self.pipeline]
    for key in ['_pipeline','pipeline']:
        child=getattr(self.pipeline,key,None)
        if child is not None:
            candidates.append(child)
    for pipeline in candidates:
        model=getattr(pipeline,'layout_det_model',None)
        if model is not None:
            cls=type(model)
            if not getattr(cls,'_benchmark_wrapped',False):
                cls.__call__=_timed(cls.__call__,'layout_detection')
                cls._benchmark_wrapped=True
    return result
TritonPythonModel.initialize=_initialize_profiled
