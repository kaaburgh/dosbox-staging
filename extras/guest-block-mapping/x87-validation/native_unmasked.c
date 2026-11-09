#define _POSIX_C_SOURCE 200809L
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
static sigjmp_buf restore;
static volatile sig_atomic_t caught;
static void on_fpe(int signal, siginfo_t *info, void *context) {
 (void)signal; (void)context; caught=info->si_code; siglongjmp(restore,1);
}
int main(void) {
 struct sigaction act={0};act.sa_sigaction=on_fpe;act.sa_flags=SA_SIGINFO;sigemptyset(&act.sa_mask);sigaction(SIGFPE,&act,0);
 if (!sigsetjmp(restore,1)) {
  unsigned short control=0x037b;float zero=0;
  __asm__ volatile("fninit; fldcw %0; fld1; fdivs %1; fwait"::"m"(control),"m"(zero):"memory");
 }
 __asm__ volatile("fninit" ::: "memory");
 printf("SIGFPE_si_code=%d\n",(int)caught);return caught==FPE_FLTDIV?0:1;
}
