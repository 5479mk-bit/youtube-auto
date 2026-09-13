from ..base import DatasetAdapter
from .business import BusinessAdapter
from .fire import FireAdapter
from .population import PopulationAdapter
from .realestate import RealEstateAdapter
from .traffic import TrafficAdapter

ADAPTERS: dict[str, DatasetAdapter] = {
    adapter.name: adapter
    for adapter in (
        BusinessAdapter(),
        PopulationAdapter(),
        FireAdapter(),
        TrafficAdapter(),
        RealEstateAdapter(),
    )
}
