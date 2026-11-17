"""MCP Server for AI Operations Assistant tools using FastMCP and stdio transport."""

import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from agentd.tools.github_tool import GitHubTool
from agentd.tools.weather_tool import WeatherTool
from agentd.tools.news_tool import NewsTool
from agentd.tools.discord_tool import DiscordTool
from agentd.tools.slack_tool import SlackTool

load_dotenv()

# Runs as a subprocess whose stderr reaches the user's terminal; keep the MCP
# library's per-request INFO lines out of it.
logging.getLogger("mcp").setLevel(logging.WARNING)

# Initialize FastMCP server
mcp = FastMCP("AIOpsAssistantTools")

# Instantiate underlying tools
github_tool_instance = GitHubTool()
weather_tool_instance = WeatherTool()
news_tool_instance = NewsTool()
discord_tool_instance = DiscordTool()
slack_tool_instance = SlackTool()


@mcp.tool()
def github_search_repos(query: str, per_page: int = 10, sort: str = "stars", order: str = "desc") -> Dict[str, Any]:
    """Search GitHub repositories matching a query string."""
    response = github_tool_instance.call({
        "operation": "search_repos",
        "query": query,
        "per_page": per_page,
        "sort": sort,
        "order": order
    })
    return response.to_dict()


@mcp.tool()
def github_get_repo(owner: str, repo: str) -> Dict[str, Any]:
    """Get detailed information for a specific GitHub repository."""
    response = github_tool_instance.call({
        "operation": "get_repo",
        "owner": owner,
        "repo": repo
    })
    return response.to_dict()


@mcp.tool()
def github_get_repos_batch(repo_list: List[str]) -> Dict[str, Any]:
    """Get details for multiple GitHub repositories at once."""
    response = github_tool_instance.call({
        "operation": "get_repos_batch",
        "repo_list": repo_list
    })
    return response.to_dict()


@mcp.tool()
def weather_current(city: str, units: str = "metric") -> Dict[str, Any]:
    """Get current weather conditions for a specified city."""
    response = weather_tool_instance.call({
        "operation": "current_weather",
        "city": city,
        "units": units
    })
    return response.to_dict()


@mcp.tool()
def weather_forecast(city: str, units: str = "metric", cnt: int = 5) -> Dict[str, Any]:
    """Get weather forecast for a specified city."""
    response = weather_tool_instance.call({
        "operation": "forecast",
        "city": city,
        "units": units,
        "cnt": cnt
    })
    return response.to_dict()


@mcp.tool()
def news_search(query: str, language: str = "en", page_size: int = 10) -> Dict[str, Any]:
    """Search news articles matching a keyword query."""
    response = news_tool_instance.call({
        "operation": "search_news",
        "query": query,
        "language": language,
        "page_size": page_size
    })
    return response.to_dict()


@mcp.tool()
def news_top_headlines(category: Optional[str] = None, country: str = "us", page_size: int = 10) -> Dict[str, Any]:
    """Get top headlines by category or country."""
    args = {
        "operation": "top_headlines",
        "country": country,
        "page_size": page_size
    }
    if category:
        args["category"] = category
    response = news_tool_instance.call(args)
    return response.to_dict()


@mcp.tool()
def discord_list_channels(guild_id: str) -> Dict[str, Any]:
    """List text channels of a Discord server (guild). Returns channel ids and names."""
    return discord_tool_instance.call({
        "operation": "list_channels",
        "guild_id": guild_id
    }).to_dict()


@mcp.tool()
def discord_get_messages(channel_id: str, limit: int = 50, pages: int = 1, guild_id: str = "") -> Dict[str, Any]:
    """Read recent messages from a Discord channel, newest first, with author, content and jump links."""
    return discord_tool_instance.call({
        "operation": "get_messages",
        "channel_id": channel_id,
        "limit": limit,
        "pages": pages,
        "guild_id": guild_id
    }).to_dict()


@mcp.tool()
def discord_search_messages(channel_id: str, query: str, limit: int = 25, pages: int = 3, guild_id: str = "") -> Dict[str, Any]:
    """Search a Discord channel's recent history for messages containing a keyword, with jump links."""
    return discord_tool_instance.call({
        "operation": "search_messages",
        "channel_id": channel_id,
        "query": query,
        "limit": limit,
        "pages": pages,
        "guild_id": guild_id
    }).to_dict()


@mcp.tool()
def slack_list_channels() -> Dict[str, Any]:
    """List Slack channels the bot can see, with ids and names."""
    return slack_tool_instance.call({
        "operation": "list_channels"
    }).to_dict()


@mcp.tool()
def slack_get_messages(channel_id: str, limit: int = 50) -> Dict[str, Any]:
    """Read recent messages from a Slack channel, newest first, with text and permalinks."""
    return slack_tool_instance.call({
        "operation": "get_messages",
        "channel_id": channel_id,
        "limit": limit
    }).to_dict()


@mcp.tool()
def slack_search_messages(channel_id: str, query: str, limit: int = 25) -> Dict[str, Any]:
    """Search a Slack channel's history for messages containing a keyword, with permalinks."""
    return slack_tool_instance.call({
        "operation": "search_messages",
        "channel_id": channel_id,
        "query": query,
        "limit": limit
    }).to_dict()


if __name__ == "__main__":
    mcp.run(transport="stdio")
