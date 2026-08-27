import asyncio
import sys
sys.path.insert(0, "/app")
from scripts.inspect_live_source import main
asyncio.run(main())