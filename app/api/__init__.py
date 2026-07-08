from app.models.section import Section
from sqlalchemy.ext.asyncio import AsyncSession


async def updateSectionCalcStatus(db: AsyncSession, section_id):
    section = await db.get(Section, section_id)
    if section and section.is_calculated:
        setattr(section, "is_calculated", False)
        await db.commit()
