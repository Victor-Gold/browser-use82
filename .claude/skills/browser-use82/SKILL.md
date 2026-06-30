```markdown
# browser-use82 Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill provides guidance on the development patterns, coding conventions, and workflows used in the `browser-use82` Python repository. It covers file organization, commit message standards, import/export styles, and testing practices, enabling contributors to maintain consistency and quality throughout the codebase.

## Coding Conventions

### File Naming
- Use **snake_case** for all file and module names.
  - Example: `user_agent_parser.py`

### Import Style
- Prefer **relative imports** within the package.
  - Example:
    ```python
    from .utils import parse_headers
    ```

### Export Style
- Use **named exports** to control what is accessible from modules.
  - Example:
    ```python
    __all__ = ['parse_headers', 'UserAgent']
    ```

### Commit Messages
- Follow **conventional commit** patterns.
- Use prefixes such as `refactor` and `style`.
- Keep commit messages concise (average ~54 characters).
  - Example:
    ```
    refactor: improve header parsing logic
    style: reformat code for PEP8 compliance
    ```

## Workflows

### Refactoring Code
**Trigger:** When improving code structure or readability without changing its external behavior  
**Command:** `/refactor`

1. Identify code that can be simplified or better organized.
2. Make changes ensuring no external behavior is altered.
3. Use relative imports and maintain snake_case naming.
4. Commit with a message starting with `refactor:`.
   - Example: `refactor: extract header parsing to utils module`
5. Run tests to ensure no regressions.

### Code Styling
**Trigger:** When updating formatting, whitespace, or other stylistic elements  
**Command:** `/style`

1. Review code for PEP8 compliance and stylistic consistency.
2. Apply formatting changes as needed.
3. Commit with a message starting with `style:`.
   - Example: `style: fix indentation and remove trailing spaces`
4. Run tests to confirm no unintended changes.

## Testing Patterns

- Test files follow the pattern: `*.test.*`
  - Example: `parser.test.py`
- The specific testing framework is not specified; ensure tests are discoverable and runnable.
- Place test files alongside the code they test or in a dedicated test directory.
- Example test structure:
  ```python
  def test_parse_headers():
      assert parse_headers('User-Agent: test') == {'User-Agent': 'test'}
  ```

## Commands
| Command    | Purpose                                      |
|------------|----------------------------------------------|
| /refactor  | Start a code refactoring workflow            |
| /style     | Begin a code styling/formatting workflow     |
```
