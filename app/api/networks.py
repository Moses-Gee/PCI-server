from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.models.network import Network
from app.schemas.network import NetworkCreate, NetworkUpdate, NetworkResponse

router = APIRouter(prefix="/networks", tags=["Networks"])


@router.get("/", response_model=List[NetworkResponse])
async def get_networks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Network).order_by(Network.created_at.desc()))
    networks = result.scalars().all()
    return networks


@router.post("/", response_model=NetworkResponse, status_code=status.HTTP_201_CREATED)
async def create_network(network: NetworkCreate, db: AsyncSession = Depends(get_db)):
    db_network = Network(**network.dict())
    db.add(db_network)
    await db.commit()
    await db.refresh(db_network)
    return db_network


@router.get("/{network_id}", response_model=NetworkResponse)
async def get_network(network_id: UUID, db: AsyncSession = Depends(get_db)):
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    return network


@router.patch("/{network_id}", response_model=NetworkResponse)
async def update_network(
    network_id: UUID, update: NetworkUpdate, db: AsyncSession = Depends(get_db)
):
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    for key, value in update.dict(exclude_unset=True).items():
        setattr(network, key, value)
    await db.commit()
    await db.refresh(network)
    return network


@router.delete("/{network_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network(network_id: UUID, db: AsyncSession = Depends(get_db)):
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    await db.delete(network)
    await db.commit()
