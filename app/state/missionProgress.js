export function getMissionsByJobCode(allMissions, jobCode) {
  return allMissions.filter((mission) => mission.job_code === jobCode);
}

export function getCurrentMission({ allMissions, state }) {
  const jobCode = state.selectedJobs[state.missionJobIndex];
  return getMissionsByJobCode(allMissions, jobCode)[state.missionStepIndex] || null;
}

export function getTotalMissionCount({ allMissions, selectedJobs }) {
  return selectedJobs.reduce((sum, jobCode) => sum + getMissionsByJobCode(allMissions, jobCode).length, 0);
}

export function getDoneMissionCount({ allMissions, state }) {
  let done = 0;
  for (let idx = 0; idx < state.missionJobIndex; idx += 1) {
    done += getMissionsByJobCode(allMissions, state.selectedJobs[idx]).length;
  }
  return done + state.missionStepIndex;
}

export function isLastMission({ allMissions, state }) {
  const jobCode = state.selectedJobs[state.missionJobIndex];
  const missions = getMissionsByJobCode(allMissions, jobCode);
  return state.missionJobIndex === state.selectedJobs.length - 1
    && state.missionStepIndex === missions.length - 1;
}
