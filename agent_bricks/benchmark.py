"""
Benchmark script to compare OpenAI Agents SDK vs Agent Bricks Supervisor.

Runs the same queries against both versions and measures:
- Time to first token (TTFT)
- Total response time
- Response quality (manual review)
"""

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import httpx
from databricks.sdk import WorkspaceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result from a single query."""
    query: str
    version: str  # "openai-sdk" or "agent-bricks"
    ttft_ms: float  # Time to first token
    total_time_ms: float  # Total response time
    response: str  # Full response text
    error: str | None = None
    timestamp: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


class BenchmarkRunner:
    """Run benchmarks against both agent versions."""
    
    def __init__(self, profile: str = "brickbot"):
        self.w = WorkspaceClient(profile=profile)
        self.host = self.w.config.host
        self.token = self.w.config.token
        
        # Endpoint names
        self.openai_sdk_endpoint = os.environ.get(
            "OPENAI_SDK_ENDPOINT", "brickbot"
        )
        self.agent_bricks_endpoint = os.environ.get(
            "AGENT_BRICKS_ENDPOINT", "brickbot-supervisor"
        )
    
    async def query_endpoint(
        self, 
        endpoint: str, 
        query: str,
        stream: bool = True,
    ) -> tuple[float, float, str]:
        """
        Query an endpoint and measure latency.
        
        Returns:
            (ttft_ms, total_time_ms, response_text)
        """
        url = f"{self.host}/serving-endpoints/{endpoint}/invocations"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        
        if stream:
            headers["Accept"] = "text/event-stream"
        
        payload = {
            "messages": [{"role": "user", "content": query}],
            "stream": stream,
        }
        
        start_time = time.perf_counter()
        ttft = None
        response_text = ""
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if ttft is None:
                            ttft = (time.perf_counter() - start_time) * 1000
                        
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() and data != "[DONE]":
                                try:
                                    chunk = json.loads(data)
                                    if "choices" in chunk:
                                        for choice in chunk["choices"]:
                                            delta = choice.get("delta", {})
                                            if "content" in delta:
                                                response_text += delta["content"]
                                except json.JSONDecodeError:
                                    pass
            else:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                ttft = (time.perf_counter() - start_time) * 1000
                result = response.json()
                if "choices" in result:
                    response_text = result["choices"][0]["message"]["content"]
        
        total_time = (time.perf_counter() - start_time) * 1000
        
        return ttft or total_time, total_time, response_text
    
    async def run_query(
        self, 
        query: str, 
        version: str,
    ) -> BenchmarkResult:
        """Run a single query and return the result."""
        
        endpoint = (
            self.openai_sdk_endpoint if version == "openai-sdk" 
            else self.agent_bricks_endpoint
        )
        
        try:
            ttft, total_time, response = await self.query_endpoint(endpoint, query)
            return BenchmarkResult(
                query=query,
                version=version,
                ttft_ms=ttft,
                total_time_ms=total_time,
                response=response,
            )
        except Exception as e:
            logger.error(f"Error querying {version}: {e}")
            return BenchmarkResult(
                query=query,
                version=version,
                ttft_ms=0,
                total_time_ms=0,
                response="",
                error=str(e),
            )
    
    async def run_benchmark(
        self, 
        queries: list[str],
        versions: list[str] = ["openai-sdk", "agent-bricks"],
    ) -> list[BenchmarkResult]:
        """Run benchmark across all queries and versions."""
        
        results = []
        
        for i, query in enumerate(queries):
            logger.info(f"Query {i+1}/{len(queries)}: {query[:50]}...")
            
            for version in versions:
                logger.info(f"  Testing {version}...")
                result = await self.run_query(query, version)
                results.append(result)
                
                if result.error:
                    logger.error(f"    Error: {result.error}")
                else:
                    logger.info(f"    TTFT: {result.ttft_ms:.0f}ms, Total: {result.total_time_ms:.0f}ms")
                
                # Small delay between requests
                await asyncio.sleep(0.5)
        
        return results


def generate_report(results: list[BenchmarkResult]) -> dict:
    """Generate a summary report from benchmark results."""
    
    by_version = {}
    for r in results:
        if r.version not in by_version:
            by_version[r.version] = []
        by_version[r.version].append(r)
    
    summary = {}
    for version, version_results in by_version.items():
        successful = [r for r in version_results if not r.error]
        
        if successful:
            ttfts = [r.ttft_ms for r in successful]
            totals = [r.total_time_ms for r in successful]
            
            summary[version] = {
                "queries": len(version_results),
                "successful": len(successful),
                "failed": len(version_results) - len(successful),
                "ttft_avg_ms": sum(ttfts) / len(ttfts),
                "ttft_p50_ms": sorted(ttfts)[len(ttfts) // 2],
                "ttft_p95_ms": sorted(ttfts)[int(len(ttfts) * 0.95)] if len(ttfts) > 1 else ttfts[0],
                "total_avg_ms": sum(totals) / len(totals),
                "total_p50_ms": sorted(totals)[len(totals) // 2],
                "total_p95_ms": sorted(totals)[int(len(totals) * 0.95)] if len(totals) > 1 else totals[0],
            }
        else:
            summary[version] = {
                "queries": len(version_results),
                "successful": 0,
                "failed": len(version_results),
            }
    
    return summary


# Default test queries
DEFAULT_QUERIES = [
    "What sessions are about machine learning?",
    "Where is the keynote?",
    "What's the WiFi password?",
    "Who is speaking about LLMs?",
    "What are the lunch options?",
    "Is the venue wheelchair accessible?",
    "What exhibitors are in the expo hall?",
    "What time does registration open?",
    "Tell me about the Databricks booth",
    "What sessions does Matei Zaharia have?",
]


async def main():
    parser = argparse.ArgumentParser(description="Benchmark OpenAI SDK vs Agent Bricks")
    parser.add_argument("--queries", type=str, help="JSON file with queries")
    parser.add_argument("--output", type=str, default="benchmark_results.json", help="Output file")
    parser.add_argument("--profile", type=str, default="brickbot", help="Databricks profile")
    parser.add_argument("--versions", type=str, nargs="+", default=["openai-sdk", "agent-bricks"])
    args = parser.parse_args()
    
    # Load queries
    if args.queries:
        with open(args.queries) as f:
            queries = json.load(f)
    else:
        queries = DEFAULT_QUERIES
    
    logger.info(f"Running benchmark with {len(queries)} queries")
    logger.info(f"Versions: {args.versions}")
    
    # Run benchmark
    runner = BenchmarkRunner(profile=args.profile)
    results = await runner.run_benchmark(queries, args.versions)
    
    # Generate report
    summary = generate_report(results)
    
    # Output
    output = {
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    
    logger.info(f"Results written to {args.output}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    for version, stats in summary.items():
        print(f"\n{version}:")
        if stats.get("successful", 0) > 0:
            print(f"  Queries: {stats['successful']}/{stats['queries']} successful")
            print(f"  TTFT:  avg={stats['ttft_avg_ms']:.0f}ms  p50={stats['ttft_p50_ms']:.0f}ms  p95={stats['ttft_p95_ms']:.0f}ms")
            print(f"  Total: avg={stats['total_avg_ms']:.0f}ms  p50={stats['total_p50_ms']:.0f}ms  p95={stats['total_p95_ms']:.0f}ms")
        else:
            print(f"  All {stats['queries']} queries failed")


if __name__ == "__main__":
    asyncio.run(main())
