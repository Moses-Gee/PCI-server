from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select, func
from typing import List
from uuid import UUID

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.network import Network
from app.models.sample_unit import SampleUnit
from app.models.section import Section
from app.models.user import User
from app.schemas.network import (
    NetworkCreate,
    NetworkUpdate,
    NetworkResponse,
    NetworkWithSectionsResponse,
)
from app.services.image_service import delete_images_for_ids

router = APIRouter(prefix="/networks", tags=["Networks"])


@router.get("/", response_model=List[NetworkWithSectionsResponse])
async def get_networks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(Network)
        .options(selectinload(Network.sections))
        .where(Network.user_id == current_user.id)
        # .options(selectinload(Network.sections).order_by(Section.chainage_start))
        .order_by(Network.created_at.desc())
    )
    result = await db.execute(stmt)
    networks = result.scalars().all()
    return networks


# @router.get("/", response_model=List[NetworkResponse])
# async def get_networks(db: AsyncSession = Depends(get_db)):
#     result = await db.execute(select(Network).order_by(Network.created_at.desc()))
#     networks = result.scalars().all()
#     return networks


@router.post("/", response_model=NetworkResponse, status_code=status.HTTP_201_CREATED)
async def create_network(
    network: NetworkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_network = Network(**network.model_dump(), user_id=current_user.id)
    db.add(db_network)
    await db.commit()
    await db.refresh(db_network)
    return db_network


@router.get("/{network_id}", response_model=NetworkWithSectionsResponse)
async def get_network(
    network_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(Network)
        .where(Network.id == network_id)
        .options(selectinload(Network.sections))
    )
    result = await db.execute(stmt)
    network = result.scalar_one_or_none()
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    # Make sure user owns this network
    if network.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return network


# @router.get("/{network_id}", response_model=NetworkResponse)
# async def get_network(network_id: UUID, db: AsyncSession = Depends(get_db)):
#     network = await db.get(Network, network_id)
#     if not network:
#         raise HTTPException(status_code=404, detail="Network not found")
#     return network


@router.patch("/{network_id}", response_model=NetworkResponse)
async def update_network(
    network_id: UUID,
    payload: NetworkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    if network.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(network, key, value)
    await db.commit()
    await db.refresh(network)
    return network


@router.delete("/{network_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network(
    network_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    if network.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Collect sample unit IDs across all sections
    stmt = (
        select(SampleUnit.id)
        .join(Section, SampleUnit.section_id == Section.id)
        .where(Section.network_id == network_id)
    )
    result = await db.execute(stmt)
    sample_unit_ids = result.scalars().all()

    await delete_images_for_ids(db, sample_unit_ids)
    await db.delete(network)
    await db.commit()
