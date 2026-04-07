# Work Plan: Implement Contextual Command Chaining for /bot Commands

## Objective
Enhance the `/bot` command system to maintain contextual awareness between chained requests, allowing users to naturally continue workflows like:
1. `/bot create store channel`
2. `/bot create embed` 
3. `/bot create modal`

Where each subsequent command understands what was created in the previous step.

## Scope
**IN:**
- Enhancing AI contextual awareness through conversation history
- Improving system prompt to encourage contextual referencing
- Ensuring summaries include specific, referenceable details
- Maintaining backward compatibility
- Following existing patterns in the codebase

**OUT:**
- Major architectural changes to the AI system
- Changes to core Discord API interaction logic
- Modifications to data persistence layer
- Changes to user interface beyond textual improvements

## Detailed Implementation Plan

### Phase 1: Enhance System Prompt for Contextual Awareness
- Update `SYSTEM_PROMPT` in `ai_client.py` to:
  - Explicitly instruct AI to reference conversation history
  - Guide AI to build upon previous work when appropriate
  - Encourage specific, detailed summaries for future reference
  - Add guidance for chained operations recognition

### Phase 2: Improve Summary Detail for Contextual References
- Modify the summary generation to include:
  - Exact names of created channels, roles, commands
  - Specific IDs where relevant
  - Clear descriptions of what was created
  - Examples: "Created channel 'store'", "Created role 'VIP Member'", "Registered prefix command '!buystore'"

### Phase 3: Enhance AI Reasoning to Explicitly Reference History
- Instruct the AI's reasoning to:
  - Reference specific previous actions when relevant
  - Explain how current request builds on prior work
  - Acknowledge what was previously created
  - Example: "Since the user previously requested a store channel was created, I will now..."

### Phase 4: Validate and Test Chained Workflows
- Test common chaining patterns:
  - Channel → Embed → Modal
  - Role → Permission Setup → Assignment Command
  - Shop Channel → Shop Embed → Buy Command
  - Verification Channel → Verification Embed → Verify Button
- Ensure each step properly references the previous step's outputs

## Acceptance Criteria
- [ ] User can successfully chain: `/bot create store channel` → `/bot create embed` → `/bot create modal`
- [ ] Each command in the chain understands what was created previously
- [ ] Summaries include specific, referenceable details
- [ ] No breaking changes to existing functionality
- [ ] Contextual awareness works across different types of creations (channels, roles, commands, embeds, etc.)

## Implementation Notes
- Leverage existing conversation history mechanism in `ai_client.py`
- Build upon the current JSON action/response format
- Ensure enhancements are compatible with current action dispatcher in `actions.py`
- Test with both simple and complex chaining scenarios

## Estimated Effort
- Research/Planning: Completed (this plan)
- Implementation: 2-3 hours
- Testing: 1-2 hours
- Total: 3-5 hours

## Risk Assessment
- Low risk: Changes are primarily to prompts and response formatting
- Medium risk: Ensure we don't break existing strict JSON output requirements
- Mitigation: Thoroughly test that AI still outputs valid JSON after enhancements