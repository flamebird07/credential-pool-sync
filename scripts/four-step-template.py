#!/usr/bin/env python3
"""
Four-Step Method Template
========================

This template provides a structured approach for executing four-step method reviews
and optimizations. Follow this pattern for consistent, thorough task execution.

Usage:
    python four-step-template.py <task_description>

Template Structure:
    Step 1: Codex CLI Review (--ephemeral)
    Step 2: Codex CLI Solution (--ephemeral, read-only)
    Step 3: Codex CLI Implementation (-s danger-full-access)
    Step 4: MiMo Code Review

Example:
    python four-step-template.py "Fix Feishu UI status management chaos"
"""

import argparse
import subprocess
import sys
from pathlib import Path

def step1_review(task_description):
    """Step 1: Codex CLI Review - Understand the problem"""
    print(f"\n{'='*60}")
    print(f"STEP 1: Codex CLI Review - {task_description}")
    print(f"{'='*60}")
    
    # Create review prompt
    prompt = f"""Review the codebase for {task_description}. 

Requirements:
- Read relevant source files
- Identify specific issues and their root causes
- Confirm problems exist before proceeding
- Output detailed findings with file locations
- Do NOT suggest fixes yet

Use --ephemeral --skip-git-repo-check flags."""
    
    # Write prompt to file
    prompt_file = Path("temp_step1_review.md")
    prompt_file.write_text(prompt)
    
    # Execute Step 1
    cmd = f'cmd.exe /c "type {prompt_file} | codex exec -m gpt-5.6-luna --ephemeral --skip-git-repo-check" 2>&1 | tail -80'
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Step 1 Review completed")
        return True
    else:
        print(f"❌ Step 1 Review failed: {result.stderr}")
        return False

def step2_solution(task_description):
    """Step 2: Codex CLI Solution - Create detailed fix plan"""
    print(f"\n{'='*60}")
    print(f"STEP 2: Codex CLI Solution - {task_description}")
    print(f"{'='*60}")
    
    # Create solution prompt
    prompt = f"""Create a detailed fix plan for {task_description}.

Requirements:
- Build upon Step 1 findings
- Provide specific, actionable steps
- Include code examples where relevant
- Address root causes, not symptoms
- Use --ephemeral --skip-git-repo-check flags (read-only)
- Do NOT modify any files

Output should be a clear implementation plan."""
    
    # Write prompt to file
    prompt_file = Path("temp_step2_solution.md")
    prompt_file.write_text(prompt)
    
    # Execute Step 2
    cmd = f'cmd.exe /c "type {prompt_file} | codex exec -m gpt-5.6-luna --ephemeral --skip-git-repo-check" 2>&1 | tail -80'
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Step 2 Solution completed")
        return True
    else:
        print(f"❌ Step 2 Solution failed: {result.stderr}")
        return False

def step3_implementation(task_description):
    """Step 3: Codex CLI Implementation - Execute the fix"""
    print(f"\n{'='*60}")
    print(f"STEP 3: Codex CLI Implementation - {task_description}")
    print(f"{'='*60}")
    
    # Declare CLI tool (required)
    print("CLI Declaration: Step 3 implementation CLI: Codex CLI -s danger-full-access")
    
    # Create implementation prompt
    prompt = f"""Implement the fix plan for {task_description}.

Requirements:
- Follow the detailed plan from Step 2
- Make precise, targeted changes
- Use -s danger-full-access flag
- Include proper error handling
- Maintain code quality standards
- Add comments explaining changes

Execute the implementation now."""
    
    # Write prompt to file
    prompt_file = Path("temp_step3_implementation.md")
    prompt_file.write_text(prompt)
    
    # Execute Step 3
    cmd = f'cmd.exe /c "type {prompt_file} | codex exec -m gpt-5.6-luna -s danger-full-access --skip-git-repo-check" 2>&1 | tail -100'
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Step 3 Implementation completed")
        return True
    else:
        print(f"❌ Step 3 Implementation failed: {result.stderr}")
        return False

def step4_review(task_description):
    """Step 4: MiMo Code Review - Verify implementation"""
    print(f"\n{'='*60}")
    print(f"STEP 4: MiMo Code Review - {task_description}")
    print(f"{'='*60}")
    
    # Create review prompt
    prompt = f"""Review the implementation for {task_description}.

Requirements:
- Use MiMo Code for final verification
- Check if fixes address root causes
- Verify no new issues were introduced
- Test actual functionality (not just code review)
- Identify any remaining problems
- Provide clear pass/fail assessment

Execute thorough testing and validation."""
    
    # Write prompt to file
    prompt_file = Path("temp_step4_review.md")
    prompt_file.write_text(prompt)
    
    # Execute Step 4
    cmd = f'mimo run --model xiaomi/mimo-v2.5 "{prompt}" 2>&1 | tail -60'
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Step 4 Review completed")
        return True
    else:
        print(f"❌ Step 4 Review failed: {result.stderr}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Four-Step Method Template")
    parser.add_argument("task", help="Description of the task to execute")
    args = parser.parse_args()
    
    print("Four-Step Method Template")
    print(f"Task: {args.task}")
    
    # Execute all four steps
    steps = [
        (step1_review, "Review"),
        (step2_solution, "Solution"), 
        (step3_implementation, "Implementation"),
        (step4_review, "Review")
    ]
    
    for step_func, step_name in steps:
        if not step_func(args.task):
            print(f"\n❌ {step_name} failed. Stopping execution.")
            sys.exit(1)
    
    print(f"\n{'='*60}")
    print("🎉 ALL FOUR STEPS COMPLETED SUCCESSFULLY!")
    print(f"{'='*60}")
    print("Next steps:")
    print("1. Perform Post-Completion Self-Audit")
    print("2. Update documentation in Obsidian")
    print("3. Update task status")
    print("4. Verify actual functionality")

if __name__ == "__main__":
    main()