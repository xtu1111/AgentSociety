export const spawn = () => {
  throw new Error('child_process.spawn is not supported in this environment');
};
export default { spawn };