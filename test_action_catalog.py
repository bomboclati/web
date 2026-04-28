"""
Tests for the Action Catalog and Resolver System

This module tests the action resolver functionality.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from action_catalog import (
    ACTION_CATALOG,
    CATEGORY_GROUPS,
    resolve_user_request,
    extract_parameters_from_request,
    get_action_info,
    get_actions_by_category,
    get_all_categories,
    action_resolver,
    ActionResolver
)


def test_all_actions_have_required_fields():
    """Test that all actions in catalog have required fields."""
    required_fields = ["name", "description", "category", "parameters", "keywords"]
    missing = []
    
    for action_name, action_info in ACTION_CATALOG.items():
        for field in required_fields:
            if field not in action_info:
                missing.append((action_name, field))
    
    assert len(missing) == 0, f"Missing fields: {missing}"
    print("✓ All actions have required fields")


def test_categories_have_actions():
    """Test that all categories have actions assigned."""
    empty_categories = []
    for category, actions in CATEGORY_GROUPS.items():
        if not actions:
            empty_categories.append(category)
    
    assert len(empty_categories) == 0, f"Empty categories: {empty_categories}"
    print("✓ All categories have actions")


def test_resolve_create_channel():
    """Test resolving 'create a new channel called general'."""
    results = resolve_user_request("create a new channel called general")
    assert len(results) > 0
    
    best_match = results[0]
    assert best_match[0] == "create_channel"
    assert best_match[1] > 0.5  # High confidence
    print(f"✓ Resolved 'create channel' to: {best_match[0]} (confidence: {best_match[1]:.2f})")


def test_resolve_ban_user():
    """Test resolving 'ban user John for spam'."""
    results = resolve_user_request("ban user John for spam")
    assert len(results) > 0
    
    best_match = results[0]
    assert best_match[0] == "ban_user"
    print(f"✓ Resolved 'ban user' to: {best_match[0]} (confidence: {best_match[1]:.2f})")


def test_resolve_create_role():
    """Test resolving 'create a moderator role'."""
    results = resolve_user_request("create a moderator role")
    assert len(results) > 0
    
    best_match = results[0]
    assert "create_role" in best_match[0]
    print(f"✓ Resolved 'create role' to: {best_match[0]} (confidence: {best_match[1]:.2f})")


def test_extract_channel_name():
    """Test extracting channel name from request."""
    params = extract_parameters_from_request(
        "create a channel called general in the main category",
        "create_channel"
    )
    assert "name" in params
    print(f"✓ Extracted channel name: {params.get('name')}")


def test_extract_user_mention():
    """Test extracting user ID from mention."""
    params = extract_parameters_from_request(
        "kick <@!123456789> for spamming",
        "kick_user"
    )
    assert "user_id" in params
    assert params["user_id"] == 123456789
    print(f"✓ Extracted user ID: {params.get('user_id')}")


def test_extract_reason():
    """Test extracting reason from request."""
    params = extract_parameters_from_request(
        "warn user John because they broke the rules",
        "warn_user"
    )
    assert "reason" in params
    print(f"✓ Extracted reason: {params.get('reason')}")


def test_action_resolver_get_best_match():
    """Test ActionResolver.get_best_match method."""
    resolver = ActionResolver()
    best = resolver.get_best_match("create a verification system")
    
    assert best is not None
    assert "action" in best
    assert "confidence" in best
    assert "parameters" in best
    print(f"✓ Best match: {best['action']} (confidence: {best['confidence']:.2f})")


def test_validate_parameters_valid():
    """Test parameter validation with valid params."""
    valid, errors = action_resolver.validate_parameters(
        "create_channel",
        {"name": "test-channel"}
    )
    assert valid is True
    assert len(errors) == 0
    print("✓ Valid parameters validated correctly")


def test_validate_parameters_missing_required():
    """Test parameter validation with missing required param."""
    valid, errors = action_resolver.validate_parameters(
        "create_channel",
        {}  # Missing required 'name'
    )
    assert valid is False
    assert len(errors) > 0
    print(f"✓ Missing parameter detected: {errors}")


def test_all_actions_implemented():
    """Test that all catalog actions have corresponding implementations."""
    from actions import ActionHandler
    
    handler = ActionHandler(None)
    missing_impls = []
    
    for action_name in ACTION_CATALOG.keys():
        method_name = f"action_{action_name}"
        if not hasattr(handler, method_name):
            missing_impls.append(action_name)
    
    assert len(missing_impls) == 0, f"Missing implementations: {missing_impls}"
    print("✓ All catalog actions have implementations")


def test_action_info_retrieval():
    """Test getting action info."""
    info = get_action_info("create_channel")
    assert info is not None
    assert info["name"] == "create_channel"
    assert "parameters" in info
    print("✓ Action info retrieval works")


def test_get_actions_by_category():
    """Test getting actions by category."""
    channel_actions = get_actions_by_category("Channel Management")
    assert len(channel_actions) > 0
    assert "create_channel" in channel_actions
    print(f"✓ Channel Management has {len(channel_actions)} actions")


def run_all_tests():
    """Run all tests."""
    print("\n=== Running Action Catalog Tests ===\n")
    
    test_all_actions_have_required_fields()
    test_categories_have_actions()
    test_resolve_create_channel()
    test_resolve_ban_user()
    test_resolve_create_role()
    test_extract_channel_name()
    test_extract_user_mention()
    test_extract_reason()
    test_action_resolver_get_best_match()
    test_validate_parameters_valid()
    test_validate_parameters_missing_required()
    test_all_actions_implemented()
    test_action_info_retrieval()
    test_get_actions_by_category()
    
    print("\n=== All Tests Passed! ===\n")


if __name__ == "__main__":
    run_all_tests()