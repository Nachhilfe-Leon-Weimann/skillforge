run:
    @echo "Starting the SkillForge API server..."
    @uvicorn skillforge.main:app --reload
