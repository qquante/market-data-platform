"""Authenticated research control and immutable, explicitly requested exports."""
from fastapi import HTTPException,Request,Query
from fastapi.responses import Response
from pydantic import ValidationError
from .research_contracts import ResearchSpec
from .research_jobs import ResearchJobs,ResearchLimit
from .experiments import ExperimentSpec
from .experiment_store import ExperimentStore


async def read_definition(request,model):
    body=bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk)>16384:raise HTTPException(413,'Research definition exceeds 16KiB')
        body.extend(chunk)
    try:return model.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(422,exc.errors(include_input=False,include_context=False)) from None


def install_research_routes(app):
    from . import legacy_strategies

    def legacy_read(fn, **kwargs):
        try:return fn(**kwargs)
        except ValueError:raise HTTPException(503,'Legacy strategy source integrity failed') from None

    @app.get('/v1/research/strategies')
    async def strategies(offset:int=Query(0,ge=0,le=10000),limit:int=Query(20,ge=1,le=100),
                         source:str|None=Query(None,max_length=64),first_target:int|None=Query(None,ge=1,le=10000),
                         query:str=Query('',max_length=100)):
        return legacy_read(legacy_strategies.listing,offset=offset,limit=limit,source=source,first_target=first_target,query=query)

    def strategy_export(strategy_id=None):
        result=legacy_read(legacy_strategies.export,strategy_id=strategy_id)
        if result is None:raise HTTPException(404,'Legacy strategy not found')
        body,digest=result
        return Response(body,media_type='application/json',headers={'X-Content-SHA256':digest,
            'Content-Disposition':'attachment; filename="strategies-'+digest+'.json"'})

    @app.get('/v1/research/strategies/export')
    async def strategies_export():return strategy_export()

    @app.get('/v1/research/strategies/{strategy_id}')
    async def strategy_detail(strategy_id:str):
        result=legacy_read(legacy_strategies.detail,strategy_id=strategy_id)
        if result is None:raise HTTPException(404,'Legacy strategy not found')
        return result

    @app.get('/v1/research/strategies/{strategy_id}/export')
    async def one_strategy_export(strategy_id:str):return strategy_export(strategy_id)

    @app.post('/v1/research/jobs',status_code=202)
    async def create(request:Request):
        spec=await read_definition(request,ResearchSpec)
        try:return await ResearchJobs(request.app.state.store).create(spec)
        except ResearchLimit as exc:raise HTTPException(409,str(exc)) from None

    @app.get('/v1/research/jobs')
    async def listing(request:Request):
        return {'items':await ResearchJobs(request.app.state.store).status(),'limit':ResearchJobs.MAX_JOBS}

    @app.get('/v1/research/jobs/{job_id}')
    async def status(job_id:str,request:Request):
        rows=await ResearchJobs(request.app.state.store).status(job_id)
        if not rows:raise HTTPException(404,'Research job not found')
        return rows[0]

    @app.get('/v1/research/jobs/{job_id}/bundle')
    async def bundle(job_id:str,request:Request):
        result=await ResearchJobs(request.app.state.store).bundle(job_id)
        if result is None:raise HTTPException(409,'Research bundle not ready')
        body,digest=result
        return Response(body,media_type='application/json',headers={'X-Content-SHA256':digest,
            'Content-Disposition':'attachment; filename="research-'+digest+'.json"'})

    @app.post('/v1/research/jobs/{job_id}/experiments',status_code=202)
    async def create_experiment(job_id:str,request:Request):
        spec=await read_definition(request,ExperimentSpec)
        try:return await ExperimentStore(request.app.state.store).create(job_id,spec)
        except ResearchLimit as exc:raise HTTPException(409,str(exc)) from None

    @app.get('/v1/research/experiments')
    async def experiments(request:Request):
        return {'items':await ExperimentStore(request.app.state.store).status(),'limit':128}

    @app.get('/v1/research/experiments/{experiment_id}')
    async def experiment(experiment_id:str,request:Request):
        rows=await ExperimentStore(request.app.state.store).status(experiment_id)
        if not rows:raise HTTPException(404,'Experiment not found')
        return rows[0]

    @app.get('/v1/research/experiments/{experiment_id}/result')
    async def experiment_result(experiment_id:str,request:Request):
        result=await ExperimentStore(request.app.state.store).result(experiment_id)
        if result is None:raise HTTPException(409,'Experiment result not ready')
        body,digest=result
        return Response(body,media_type='application/json',headers={'X-Content-SHA256':digest,
            'Content-Disposition':'attachment; filename="experiment-'+digest+'.json"'})
