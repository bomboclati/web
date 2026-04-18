# Work Plan: Enhance Contextual Awareness for Chained /bot Commands

## Objective
Improve the AI's ability to understand and build upon previous requests in a conversation chain, enabling natural workflows like:
1. `/bot create store channel`
2. `/bot create embed` (understanding this should relate to the store channel)
3. `/bot create modal` (understanding this should relate to the store/embed system)

## Scope
**IN:**
- Enhancing the system prompt to encourage contextual awareness
- Improving summary specificity for better referenceability  
- Guiding the AI to explicitly reference conversation history in reasoning
- Maintaining all existing safety mechanisms (JSON format, action validation, user confirmation)
- No changes to core execution logic or action definitions

**OUT:**
- Modifying core AI interaction logic
- Changing the JSON action/response format
- Altering user confirmation or walkthrough presentation
- Modifying data persistence layer
- Changing how actions are executed in actions.py

## Detailed Implementation Plan

### Phase 1: Research & Preparation
- Review current SYSTEM_PROMPT in ai_client.py
- Analyze how conversation history is currently used
- Identify specific areas for enhancement to improve contextual chaining
- Determine what specific details should be included in summaries for reference

### Phase 2: Enhance System Prompt
- Update SYSTEM_PROMPT in ai_client.py with:
  - Explicit contextual awareness instruction
  - Guidance to reference conversation history in reasoning
  - Direction to build upon previous work when appropriate
  - Specific guidance for chained operations recognition
  - Request for detailed, referenceable summaries

### Phase 3: Validate Enhancement Approach
- Ensure proposed changes maintain strict JSON output requirement
- Verify that enhancements don't break existing action parsing
- Confirm that user confirmation flow remains intact
- Check that all safety mechanisms (validation, error handling) still work

### Phase 4: Document the Enhancement
- Create this work plan in .sisyphus/plans/
- Document the specific changes to be made
- Note expected improvements in contextual awareness
- Outline how users will benefit from chained workflows

## Expected Improvements
After implementation, users should be able to:
1. Naturally chain related requests: create channel → create embed → create modal
2. Have the AI understand implicit references ("the channel we just created")
3. Receive summaries with specific details they can reference in follow-ups
4. Build complex workflows conversationally without needing to repeat context
5. Still retain all existing safety mechanisms (walkthrough+confirmation, action validation, etc.)

## Success Criteria
- [ ] Enhanced system prompt encourages contextual awareness
- [ ] AI reasoning explicitly references conversation history when relevant
- [ ] Summaries include specific, creatable details (channel names, command names, etc.)
- [ ] Chained workflows like "store channel" → "embed" → "modal" work naturally
- [ ] All existing safety mechanisms remain functional
- [ ] No regression in existing functionality

## Implementation Notes
Changes will be limited to:
- SYSTEM_PROMPT enhancement in ai_client.py
- No changes to action definitions or execution logic
- No changes to user interface components (buttons, embeds, etc.)
- No changes to data persistence or validation layers

This approach leverages the existing conversation history mechanism while enhancing the AI's instruction to use that history effectively for contextual understanding.