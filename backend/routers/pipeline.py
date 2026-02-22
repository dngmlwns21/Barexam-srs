from fastapi import APIRouter, BackgroundTasks

router = APIRouter()


@router.post("/run", status_code=202)
async def run_pipeline(
    background_tasks: BackgroundTasks,
):
    """
    Triggers a full data pipeline run (crawl, transform, write).
    This is a long-running task, so it runs in the background.
    """
    # TODO: Implement actual pipeline logic and add task to background_tasks
    print("Pipeline run triggered (placeholder).")
    return {"message": "Pipeline run triggered. Check server logs for progress."}
